#
# Extend the prebuilt DEMUCS image (python3 + demucs + default model baked
# into TORCH_HOME=/data/models) with our worker. Only this thin layer is pushed.
#
FROM xserrat/facebook-demucs:latest

RUN python3 -m pip install --no-cache-dir minio redis requests

WORKDIR /srv
COPY worker-server.py /srv

# The base image sets ENTRYPOINT ["/bin/bash","--login","-c"]; replace it.
ENTRYPOINT [ "python3", "-u", "/srv/worker-server.py" ]
CMD []
