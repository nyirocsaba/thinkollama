# Use the official lightweight Python 3.12-slim image
FROM python:3.12-slim

# Set the working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY . .

# Expose the port the app runs on
EXPOSE 11435

# Startup command for the container
CMD ["uvicorn", "src.thinkollama:app", "--host", "0.0.0.0", "--port", "11435"]