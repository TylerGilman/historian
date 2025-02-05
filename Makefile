# Makefile

# Variables
APP_NAME=historian
BACKEND_DIR=backend
BUILD_DIR=build

# Build the backend
build:
	@echo "Building backend..."
	go build -o $(BUILD_DIR)/$(APP_NAME) ./$(BACKEND_DIR)

# Run the application
run: build
	@echo "Starting application..."
	./$(BUILD_DIR)/$(APP_NAME)

# Clean build artifacts
clean:
	@echo "Cleaning build artifacts..."
	rm -rf $(BUILD_DIR)

# Install dependencies
deps:
	@echo "Installing dependencies..."
	go mod download

# Format code
fmt:
	@echo "Formatting code..."
	go fmt ./...

# Run tests
test:
	@echo "Running tests..."
	go test ./...

# Default target
all: deps build

.PHONY: build run clean deps fmt test all
