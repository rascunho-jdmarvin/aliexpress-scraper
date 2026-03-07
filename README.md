# AliExpress Scraper

A FastAPI application that scrapes AliExpress product pages and stores structured data in Supabase, using Playwright for headless browsing.

## Running Locally with Docker

You can easily run this application locally on your machine using Docker and Docker Compose. The setup spins up three containers:
- **api**: The main FastAPI application.
- **redis**: The Redis message broker for background task queues.
- **worker**: The Celery worker to handle long-running background scraping tasks.

### Prerequisites
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed on your machine.

### Instructions

1. **Set up Environment Variables:**
   Make sure your `.env` file is present in the root directory (alongside `docker-compose.yml`) containing all necessary environment variables:
   ```env
   SUPABASE_URL=your_supabase_url
   SUPABASE_ANON_KEY=your_anon_key
   SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
   # Add any other variables you need
   ```

2. **Build and start the microservice containers:**
   ```bash
   docker compose up -d --build
   ```
   This command starts the API, Redis, and Celery worker in the background.

3. **Verify it's running:**
   Check the API health endpoint:
   ```bash
   curl http://localhost:8000/health
   ```
   Or visit `http://localhost:8000/docs` in your browser to view the interactive API documentation.

4. **Stop the container:**
   ```bash
   docker compose down
   ```
