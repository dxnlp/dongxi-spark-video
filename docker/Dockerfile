# The CUDA 13.0.1 Ubuntu 24.04 base used by the measured Spark installation.
FROM nvcr.io/nvidia/cuda:13.0.1-devel-ubuntu24.04@sha256:7d2f6a8c2071d911524f95061a0db363e24d27aa51ec831fcccf9e76eb72bc92
LABEL local.project="dongxi-spark-video"
COPY scripts/bootstrap.sh /tmp/bootstrap.sh
RUN bash /tmp/bootstrap.sh && rm /tmp/bootstrap.sh && rm -rf /var/lib/apt/lists/*
WORKDIR /work
