import rclpy
from avstack_bridge import Bridge
from avstack_bridge.geometry import GeometryBridge
from avstack_bridge.tracks import TrackBridge
from avstack_msgs.msg import BoxTrackArray
from avtrust.estimator import TrustEstimator
from avtrust.measurement import ViewBasedPsm
from avtrust.updater import TrustUpdater
from geometry_msgs.msg import PolygonStamped
from message_filters import ApproximateTimeSynchronizer, Subscriber
from rclpy.node import Node
from rclpy.time import Time
from tf2_ros.buffer import Buffer
from tf2_ros.transform_listener import TransformListener

from avtrust_msgs.msg import PsmArray as PsmArrayRos
from avtrust_msgs.msg import TrustArray as TrustArrayRos

from .bridge import TrustBridge


class TrustEstimatorNode(Node):
    def __init__(self, verbose: bool = False):
        super().__init__("trust_psm")
        self.verbose = verbose
        self.declare_parameter("n_agents", 4)
        self.n_agents = self.get_parameter("n_agents").value

        # initialize model
        self.model = TrustEstimator(
            measurement=ViewBasedPsm(assign_radius=2.0),
            updater=TrustUpdater(),
        )

        # qos for topics
        qos = rclpy.qos.QoSProfile(
            history=rclpy.qos.QoSHistoryPolicy.KEEP_LAST,
            depth=5,
            reliability=rclpy.qos.QoSReliabilityPolicy.RELIABLE,
            durability=rclpy.qos.QoSDurabilityPolicy.VOLATILE,
        )

        # listen to transform information
        self.tf_buffer = Buffer()
        self.tf_listener = TransformListener(self.tf_buffer, self)

        # listen to tracks from agents and cc
        self.subscriber_trks = {
            agent_ID: Subscriber(
                self,
                BoxTrackArray,
                f"/agent{agent_ID}/tracks_3d",
                qos_profile=qos,
            )
            for agent_ID in range(self.n_agents)
        }
        self.subscriber_trks["command_center"] = Subscriber(
            self,
            BoxTrackArray,
            "/command_center/tracks_3d",
            qos_profile=qos,
        )

        # listen to fov from agents
        self.subscriber_fovs = {
            agent_ID: Subscriber(
                self,
                PolygonStamped,
                f"/agent{agent_ID}/fov",
                qos_profile=qos,
            )
            for agent_ID in range(self.n_agents)
        }

        # synchronize track messages
        self.synchronizer_trks = ApproximateTimeSynchronizer(
            tuple(self.subscriber_trks.values()) + tuple(self.subscriber_fovs.values()),
            queue_size=10,
            slop=0.05,
        )
        self.synchronizer_trks.registerCallback(self.trks_fov_receive)

        # publish PSM messages
        self.publisher_agent_psms = self.create_publisher(
            PsmArrayRos,
            "psms_agents",
            qos_profile=qos,
        )
        self.publisher_track_psms = self.create_publisher(
            PsmArrayRos,
            "psms_tracks",
            qos_profile=qos,
        )

        # publish trusts
        self.publisher_agent_trust = self.create_publisher(
            TrustArrayRos,
            "trust_agents",
            qos_profile=qos,
        )
        self.publisher_track_trust = self.create_publisher(
            TrustArrayRos,
            "trust_tracks",
            qos_profile=qos,
        )

        # call reset
        self.reset()

    def reset(self):
        self.model.reset()

    def trks_fov_receive(self, *args):
        """Receive approximately synchronized tracks and fovs

        Since we set a dynamic number of agents, we have to use star input
        """
        if self.verbose:
            self.get_logger().info(f"Received {len(args)} track/fov messages!")

        ###################################################
        # Store messages
        ###################################################
        # set up the data structures -- assume things come in order
        # store track messages
        tracks_agents = {}
        for i_agent, msg in enumerate(
            args[: self.n_agents + 1]
        ):  # first are track messages
            if i_agent < (self.n_agents):
                # convert to global reference frame
                if msg.header.frame_id != "world":
                    raise NotImplementedError(
                        "There's a weird bug, so keep things in world frame"
                    )
                tracks_agents[i_agent] = TrackBridge.tracks_to_avstack(msg)
            else:
                # command center tracks are in world frame
                cc_msg = msg
                tracks_cc = TrackBridge.tracks_to_avstack(msg)
                assert cc_msg.header.frame_id == "world"

        # store FOV and pose messages
        fov_agents = {}
        position_agents = {}
        for i_agent, msg in enumerate(
            args[self.n_agents + 1 : 2 * self.n_agents + 1]
        ):  # second are fov messages
            # FOV
            fov_agents[i_agent] = GeometryBridge.polygon_to_avstack(msg)
            if msg.header.frame_id != "world":
                raise NotImplementedError("Need to convert to global here")

            # pose
            tf_world_agent = self.tf_buffer.lookup_transform(
                target_frame="world",
                source_frame=f"agent{i_agent}",
                time=Time(),  # get the latest pose
            )
            position_agents[i_agent] = GeometryBridge.position_to_avstack(
                tf_world_agent.transform.translation,
                header=tf_world_agent.header,
            )

        # log the received transforms
        # if self.verbose:
        #     pos_str = "\n".join([f"{k}:{v}" for k, v in position_agents.items()])
        #     self.get_logger().info(pos_str)

        # propagate the trusts to the current time
        timestamp = Bridge.rostime_to_time(cc_msg.header.stamp)
        self.model.updater.propagate_track_trust(timestamp)
        self.model.updater.propagate_agent_trust(timestamp)

        ###################################################
        # Run PSM generation models
        ###################################################

        # run trust model
        trust_agents, trust_tracks, psms_agents, psms_tracks = self.model(
            position_agents=position_agents,
            fov_agents=fov_agents,
            tracks_agents=tracks_agents,
            tracks_cc=tracks_cc,
        )

        # convert outputs
        psms_agents_msg = TrustBridge.psm_array_to_ros(psms_agents)
        psms_tracks_msg = TrustBridge.psm_array_to_ros(psms_tracks)
        trust_agents_msg = TrustBridge.trust_array_to_ros(trust_agents)
        trust_tracks_msg = TrustBridge.trust_array_to_ros(trust_tracks)

        # publish outputs
        self.publisher_agent_psms.publish(psms_agents_msg)
        self.publisher_track_psms.publish(psms_tracks_msg)
        self.publisher_agent_trust.publish(trust_agents_msg)
        self.publisher_track_trust.publish(trust_tracks_msg)

        # print out diagnostics
        if self.verbose:
            self.get_logger().info(str(self.model.measurement._diagnostics))
            self.get_logger().info(str(self.model.measurement._assign_diagnostics))


def main(args=None):
    rclpy.init(args=args)

    mate = TrustEstimatorNode()

    rclpy.spin(mate)

    # Destroy the node explicitly
    # (optional - otherwise it will be done automatically
    # when the garbage collector destroys the node object)
    mate.destroy_node()
    rclpy.shutdown()


if __name__ == "__main__":
    main()
