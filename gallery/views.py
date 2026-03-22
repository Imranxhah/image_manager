import csv
import io
import os
import time
import threading
import uuid
import zipfile
import urllib.request
import urllib.error
import json
import tempfile

from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponse
from django.contrib import messages

# In-memory store for download jobs {job_id: {status, done, total, downloaded, failed, zip_path}}
_JOBS = {}
_JOBS_LOCK = threading.Lock()


# ── CSV Upload ────────────────────────────────────────────────
def upload_csv(request):
    if request.method == 'POST':
        if 'csv_file' not in request.FILES:
            messages.error(request, "No file uploaded.")
            return redirect('gallery:upload')
            
        csv_file = request.FILES['csv_file']
        
        if not csv_file.name.endswith('.csv'):
            messages.error(request, "Please upload a valid CSV file.")
            return redirect('gallery:upload')

        try:
            decoded_file = csv_file.read().decode('utf-8-sig')
            lines = decoded_file.splitlines()
            if not lines:
                messages.error(request, "CSV file is empty.")
                return redirect('gallery:upload')

            reader = csv.DictReader(lines)
            raw_headers = reader.fieldnames
            if not raw_headers:
                messages.error(request, "CSV file is empty or missing headers.")
                return redirect('gallery:upload')
            
            headers = [str(h).strip() for h in raw_headers]
            
            image_url_col = None
            for idx, h in enumerate(headers):
                idx_header = raw_headers[idx]
                if 'image url' in h.lower() or 'url' in h.lower() or 'image' in h.lower():
                    image_url_col = idx_header
                    break
            
            if not image_url_col:
                messages.error(request, "Could not identify an 'Image URL' column in the CSV.")
                return redirect('gallery:upload')
            
            rows = []
            for idx, row in enumerate(reader):
                url = row.get(image_url_col)
                if url:
                    url = url.strip()
                    if url:
                        row['internal_id'] = idx
                        row['internal_image_url'] = url
                        rows.append(row)
            
            request.session['csv_headers'] = headers
            request.session['csv_data'] = rows
            request.session['image_col'] = image_url_col
            
            return redirect('gallery:gallery_view')
            
        except Exception as e:
            messages.error(request, f"Error processing file: {str(e)}")
            return redirect('gallery:upload')
            
    return render(request, 'gallery/upload.html')


# ── Gallery View ──────────────────────────────────────────────
def gallery_view(request):
    rows = request.session.get('csv_data', [])
    if not rows:
        return redirect('gallery:upload')
    return render(request, 'gallery/gallery.html', {'images': rows})


# ── Delete Image ──────────────────────────────────────────────
def delete_image(request):
    if request.method == 'POST':
        image_id = request.POST.get('id')
        if image_id is not None:
            image_id = int(image_id)
            rows = request.session.get('csv_data', [])
            new_rows = [r for r in rows if r.get('internal_id') != image_id]
            request.session['csv_data'] = new_rows
            return JsonResponse({'status': 'success', 'deleted_id': image_id})
    return JsonResponse({'status': 'error'}, status=400)


# ── Download CSV ──────────────────────────────────────────────
def download_csv(request):
    rows = request.session.get('csv_data', [])
    headers = request.session.get('csv_headers', [])
    
    if not headers or not rows:
        messages.error(request, "No data to download.")
        return redirect('gallery:upload')
        
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="cleaned_images.csv"'
    
    writer = csv.DictWriter(response, fieldnames=headers)
    writer.writeheader()
    
    for row in rows:
        clean_row = {k: v for k, v in row.items() if not k.startswith('internal_')}
        writer.writerow(clean_row)
        
    return response


# ── Background download worker ────────────────────────────────
def _download_worker(job_id, urls):
    with _JOBS_LOCK:
        _JOBS[job_id].update({'status': 'downloading', 'done': 0, 'total': len(urls),
                               'downloaded': 0, 'failed': 0})

    tmp_dir = tempfile.mkdtemp(prefix='imgmgr_')
    downloaded = 0
    failed = 0

    for i, url in enumerate(urls):
        filename = f"image_{i+1:04d}.jpg"
        filepath = os.path.join(tmp_dir, filename)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(filepath, 'wb') as f:
                    f.write(resp.read())
            downloaded += 1
        except Exception:
            failed += 1

        with _JOBS_LOCK:
            _JOBS[job_id].update({
                'done': i + 1,
                'downloaded': downloaded,
                'failed': failed,
                'percent': round(((i + 1) / len(urls)) * 100),
            })

    # Zip phase
    with _JOBS_LOCK:
        _JOBS[job_id]['status'] = 'zipping'

    zip_start = time.time()
    zip_path = os.path.join(tempfile.gettempdir(), f'imgmgr_{job_id}.zip')
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for fname in os.listdir(tmp_dir):
                zf.write(os.path.join(tmp_dir, fname), arcname=fname)
        zip_elapsed = round(time.time() - zip_start, 1)
        with _JOBS_LOCK:
            _JOBS[job_id].update({
                'status': 'done',
                'zip_path': zip_path,
                'zip_time': zip_elapsed,
                'downloaded': downloaded,
                'failed': failed,
            })
    except Exception as e:
        with _JOBS_LOCK:
            _JOBS[job_id].update({'status': 'error', 'message': str(e)})

    # Cleanup temp images
    for fname in os.listdir(tmp_dir):
        try: os.remove(os.path.join(tmp_dir, fname))
        except: pass
    try: os.rmdir(tmp_dir)
    except: pass


# ── Start Download Job ────────────────────────────────────────
def start_download(request):
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'POST required'}, status=405)

    rows = request.session.get('csv_data', [])
    urls = [r['internal_image_url'] for r in rows if r.get('internal_image_url')]

    if not urls:
        return JsonResponse({'status': 'error', 'message': 'No images to download'})

    job_id = str(uuid.uuid4())
    with _JOBS_LOCK:
        _JOBS[job_id] = {'status': 'starting', 'done': 0, 'total': len(urls),
                          'downloaded': 0, 'failed': 0, 'percent': 0}

    t = threading.Thread(target=_download_worker, args=(job_id, urls), daemon=True)
    t.start()

    return JsonResponse({'status': 'ok', 'job_id': job_id})


# ── Poll Download Status ──────────────────────────────────────
def download_status(request):
    job_id = request.GET.get('job_id')
    if not job_id:
        return JsonResponse({'status': 'error', 'message': 'No job_id'}, status=400)

    with _JOBS_LOCK:
        job = _JOBS.get(job_id)

    if not job:
        return JsonResponse({'status': 'error', 'message': 'Job not found'}, status=404)

    return JsonResponse(job)


# ── Serve Zip File ────────────────────────────────────────────
def serve_zip(request):
    job_id = request.GET.get('job_id', '')
    zip_path = os.path.join(tempfile.gettempdir(), f'imgmgr_{job_id}.zip')

    if not os.path.exists(zip_path):
        return HttpResponse("Zip not found or already downloaded.", status=404)

    with open(zip_path, 'rb') as f:
        response = HttpResponse(f.read(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="images.zip"'

    try: os.remove(zip_path)
    except: pass

    with _JOBS_LOCK:
        _JOBS.pop(job_id, None)

    return response
