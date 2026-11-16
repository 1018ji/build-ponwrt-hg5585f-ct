FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive TZ=Asia/Shanghai LANG=C.UTF-8 LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential autoconf automake bc bison flex gawk gettext git gperf \
    libelf-dev libncurses-dev libssl-dev libtool libgmp-dev libmpc-dev \
    libmpfr-dev zlib1g-dev libzstd-dev cmake ninja-build ccache curl file jq \
    patch pkgconf python3 python3-setuptools rsync unzip wget zstd \
    device-tree-compiler u-boot-tools squashfs-tools swig texinfo xsltproc \
    ca-certificates util-linux time \
    && if [ "$(dpkg --print-architecture)" = amd64 ]; then \
         apt-get install -y --no-install-recommends gcc-multilib g++-multilib; \
       fi \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1001 --shell /bin/bash builder

COPY entrypoint.sh build.sh /usr/local/bin/
RUN chmod 0755 /usr/local/bin/entrypoint.sh /usr/local/bin/build.sh
WORKDIR /workdir
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["build"]
