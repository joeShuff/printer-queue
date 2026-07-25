ARG BUILD_FROM
FROM $BUILD_FROM

# Install build deps
RUN apk add --no-cache gcc musl-dev libffi-dev

WORKDIR /app

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Copy application code
COPY app/ ./app/

# Copy and set permissions on run script
COPY addon/run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]