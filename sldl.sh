#!/bin/bash

# slsk-batchdl script using pre-built Docker image
# Usage: ./slsk-batchdl.sh [sldl options and arguments]

# Check if docker is installed
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH"
    exit 1
fi

# Create necessary directories in current directory
mkdir -p config data

echo "--- Starting sldl via Docker ---"
echo "Passing all arguments to sldl: $@"

docker run --rm \
  -v "$(pwd)/config:/config" \
  -v "$(pwd)/data:/data" \
  -e PUID=$(id -u) \
  -e PGID=$(id -g) \
  lequentindckr/slsk-batchdl \
  sldl "$@"

echo "--- sldl execution complete! ---"
echo "Check the 'data' directory for downloaded files"
echo "Configuration can be placed in the 'config' directory"