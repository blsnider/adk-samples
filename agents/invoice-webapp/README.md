# Scheels Invoice Webapp

This sample demonstrates a simple Flask application for uploading and parsing PDF invoices using Google Document AI. It includes a minimal front end styled with Scheels brand colors and supports drag-and-drop uploads of multiple PDFs. Parsed results are stored in Google Cloud Storage and BigQuery.

## Features
- Drag and drop or select multiple PDF files.
- Duplicate detection against stored invoices.
- Result page displaying parsed fields and generated summary.
- Admin page showing total invoices processed and totals by vendor.

## Running
1. From the repository root run `./setup.sh` to create a virtual environment and install dependencies.
2. Ensure Google credentials are configured and update the configuration values in `app.py`.
3. Launch the app from this directory:

```bash
python app.py
```

Visit `http://localhost:8080` to upload invoices.
