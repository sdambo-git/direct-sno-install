# OpenShift on NVIDIA DSX Air

## Expected experience

Choose the path that matches the result you want:

- **Image-based SNO (IBI):** install a single-node OpenShift cluster from a
  prepared image. This demonstrates the image-based SNO provisioning path,
  not a multi-node or high-availability cluster. With SNO on NVIDIA DSX Air we can't check, run network tests. See the
  [image-based SNO path](https://github.com/sdambo-git/direct-sno-install/tree/dsx-air-cli#nvidia-dsx-fabric-with-an-ibi-node-topology-nodpujson).
- **3-node HA MNO:** install and verify a full three-node OpenShift cluster
  with three control-plane nodes. This is the multi-node, high-availability
  experience and requires more Air resources. See the
  [3-node HA path](https://github.com/sdambo-git/direct-sno-install/tree/3-node-ha-multinode-cluster#nvidia-dsx-fabric-multinode-ha-topology-nodpujson).

These are distinct installation paths with different expected outcomes:
use image-based SNO for the single-node image-based result, and 3-node HA MNO
for the full multi-node HA experience.
