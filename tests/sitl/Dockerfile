# ArduCopter SITL for the safety-path suite, pinned to the firmware we validate against.
#
# Why this exists: tests/sitl/run.py spawns its own arducopter binary, wipes its EEPROM
# and kills the process to simulate a Pixhawk power cut, so it cannot attach to an
# already-running SITL container. It needs a binary it owns. The native macOS build
# hangs (./waf copter produces zero object files indefinitely), so the suite had no
# machine it could run on. Building on Linux here fixes that at the source.
#
# The off-the-shelf radarku/ardupilot-sitl image is NOT a substitute: it carries
# ArduCopter 4.0.3 from January 2021, five years older than the firmware we fly, and
# failsafe behaviour and parameter names moved substantially in between.
#
# Builds for the host's native architecture — SITL at --speedup 1 is not CPU-bound
# (measured 1.00x real-time at 5% CPU even under x86-on-ARM emulation), so the arch
# only affects how long this build takes.
ARG COPTER_TAG=Copter-4.6.3

FROM ubuntu:22.04 AS build
ARG COPTER_TAG
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
        git ca-certificates build-essential g++ gawk make wget rsync \
        python3 python3-dev python3-pip python3-setuptools python3-wheel \
        libtool pkg-config ccache \
    && rm -rf /var/lib/apt/lists/*
# waf's code generators need these; empy is version-sensitive and 4.x breaks the build.
RUN pip3 install --no-cache-dir "empy==3.3.4" pexpect future lxml pymavlink
WORKDIR /
RUN git clone --depth 1 --branch "${COPTER_TAG}" https://github.com/ArduPilot/ardupilot.git \
    && cd ardupilot \
    && git submodule update --init --recursive --depth 1
WORKDIR /ardupilot
# DroneCAN is off: it pulls a separate python toolchain and the ZD650 does not use it.
RUN ./waf configure --board sitl --disable-DroneCAN \
    && ./waf copter \
    && test -x build/sitl/bin/arducopter

FROM ubuntu:22.04
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC PYTHONUNBUFFERED=1
# procps: run.py reads and signals the SITL process it spawned.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-pip ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
# The only hard third-party imports on the --sim path. The camera stack
# (depthai, cv2, pupil_apriltags, gi) is imported inside functions --sim never calls.
RUN pip3 install --no-cache-dir numpy pymavlink websockets
# run.py expects $ardupilot/build/sitl/bin/arducopter and
# $ardupilot/Tools/autotest/default_params/copter.parm.
COPY --from=build /ardupilot/build/sitl/bin/arducopter /ardupilot/build/sitl/bin/arducopter
COPY --from=build /ardupilot/Tools/autotest/default_params/ /ardupilot/Tools/autotest/default_params/
RUN /ardupilot/build/sitl/bin/arducopter --help >/dev/null 2>&1 || true
WORKDIR /repo
