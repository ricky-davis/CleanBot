# Use an official Python runtime as a parent image
FROM python:3.8-slim

# Set the working directory in the container
WORKDIR /usr/src/app

# Copy the dependency files
COPY Pipfile Pipfile.lock requirements.txt ./

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's code
COPY . .

# Create a directory for persistent data
RUN mkdir /usr/src/app/data

# The command to run the bot
CMD ["python", "CleanBotman.py"]
