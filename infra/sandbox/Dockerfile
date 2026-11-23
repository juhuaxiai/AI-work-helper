FROM python:3.12-alpine

WORKDIR /opt/verification
COPY verify_tts.py .
USER 65534:65534
ENTRYPOINT ["python", "-I", "-B", "/opt/verification/verify_tts.py"]
