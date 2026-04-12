ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Python, Chromium, rclone and dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    chromium \
    rclone \
    && rm -rf /var/lib/apt/lists/*

ENV PLAYWRIGHT_BROWSERS_PATH=/usr/lib
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV CHROMIUM_PATH=/usr/bin/chromium

# Install Python dependencies
RUN pip3 install --no-cache-dir --break-system-packages playwright

# Copy add-on files
COPY twigsee_download.py /app/twigsee_download.py

ENTRYPOINT []
CMD ["python3", "/app/twigsee_download.py"]
