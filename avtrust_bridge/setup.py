import os
from glob import glob

from setuptools import find_packages, setup


package_name = "avtrust_bridge"

setup(
    name=package_name,
    version="0.0.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "config"), glob("config/*.yaml")),
        (os.path.join("share", package_name, "launch"), glob("launch/*.py")),
        (os.path.join("share", package_name, "samples"), glob("samples/*.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="spencer",
    maintainer_email="20426598+roshambo919@users.noreply.github.com",
    description="TODO: Package description",
    license="TODO: License declaration",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "estimator = avtrust_bridge.estimator:main",
            "visualizer = avtrust_bridge.visualizer:main",
            "agent_pub_sample = samples.AgentTrustSample:main",
            "track_pub_sample = samples.TrackTrustSample:main",
        ],
    },
)
