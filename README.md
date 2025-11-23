# Audibound Studio

## Setup
1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
2. **Install Redis** (Required for the job queue):
   ```bash
   brew install redis
   ```

## Running the Application
You need to run **3 separate processes** in 3 terminal tabs/windows.

### Tab 1: Redis (The Message Broker)
```bash
redis-server
```

### Tab 2: Celery Worker (The Background Processor)
```bash
celery -A src.worker.celery_app worker --loglevel=info
```

### Tab 3: Web Server (The API & UI)
```bash
uvicorn src.main:app --reload
```

## Usage
1. Open [http://localhost:8000](http://localhost:8000) in your browser.
2. Create a new project.
3. Click **Direct Script** (Uses Gemini).
4. Click **Produce Audio** (Uses Kokoro/Modal).
