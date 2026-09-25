# Third-party sources and model terms

This repository supplies a portal and orchestration wrapper. Upstream runtime source and weights are downloaded separately and are excluded from Git.

| Component | Pinned source / revision | Terms |
|---|---|---|
| FastVideo | [hao-ai-lab/FastVideo](https://github.com/hao-ai-lab/FastVideo), `8760eb7a0677cc2582ce4c990e2e1c3a121eb149` | [Apache-2.0](https://github.com/hao-ai-lab/FastVideo/blob/8760eb7a0677cc2582ce4c990e2e1c3a121eb149/LICENSE) |
| FastH3 Preview v1 VSA DataFree | [Model card](https://huggingface.co/FastVideo/FastVideo-FastH3-4-step-Preview-v1-VSA-DataFree), `5ea076f35b84da4c3c82217112fa733d8eea2ae1` | MiniMax H3 Community License, inherited by the checkpoint; read the model repository terms |
| TAEH3 decoder | [madebyollin/taehv](https://github.com/madebyollin/taehv), `62f7591f59dfbb4c3c02b7a621d180a9eeaba26c` | [Upstream license](https://github.com/madebyollin/taehv/blob/62f7591f59dfbb4c3c02b7a621d180a9eeaba26c/LICENSE) |
| CUTLASS build dependency | [NVIDIA/cutlass](https://github.com/NVIDIA/cutlass), `e67e63c331d6e4b729047c95cf6b92c8454cba89` | Upstream license |
| ThunderKittens build dependency | [HazyResearch/ThunderKittens](https://github.com/HazyResearch/ThunderKittens), `6c27e28c8115d1839d9eeeb530913c184a75fc87` | Upstream license |
| CUDA development image | `nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04`, digest `sha256:7d2f6a8c2071d911524f95061a0db363e24d27aa51ec831fcccf9e76eb72bc92` | NVIDIA container/software terms |

TAEH3 checkpoint SHA-256: `4fd022bfcab08772fe0536b17ea1a3bbb5625be11e397868d1c5d891863d4c13`.

Dependency constraints in `requirements/spark-cu130.txt` record the measured Python package versions. Those packages retain their own licenses. This repository's MIT license applies only to the original portal/orchestration code and documentation; it does not grant additional rights to third-party software, weights, training data or generated likenesses. Downloading a checkpoint is not a substitute for reviewing its terms.

Credit to Hao AI Lab/FastVideo and the FastH3 contributors, MiniMax, and the TAEHV project for the runtime, distilled checkpoint and lightweight decoder used by this portal.
