/* ============================================================
   ImageManager Pro — Main JavaScript
   Pinterest-style masonry + drag upload + AJAX delete
============================================================ */

// ── CSRF Helper ──────────────────────────────────────────────
const getCookie = name => {
    let v = null;
    if (document.cookie) {
        document.cookie.split(';').forEach(c => {
            const [k, val] = c.trim().split('=');
            if (k === name) v = decodeURIComponent(val);
        });
    }
    return v;
};
const csrftoken = getCookie('csrftoken');


// ── Upload Page ───────────────────────────────────────────────
function initUpload() {
    const fileInput = document.getElementById('csv_file');
    const dropArea  = document.getElementById('drop-area');
    const fileInfo  = document.getElementById('file-info');
    const fileNameEl= document.getElementById('file-name');
    const removeBtn = document.getElementById('remove-file');
    const submitBtn = document.getElementById('submit-btn');

    if (!fileInput || !dropArea) return;

    ['dragenter','dragover','dragleave','drop'].forEach(ev => {
        dropArea.addEventListener(ev, e => { e.preventDefault(); e.stopPropagation(); });
    });
    ['dragenter','dragover'].forEach(ev =>
        dropArea.addEventListener(ev, () => dropArea.classList.add('dragover'))
    );
    ['dragleave','drop'].forEach(ev =>
        dropArea.addEventListener(ev, () => dropArea.classList.remove('dragover'))
    );

    dropArea.addEventListener('drop', e => handleFiles(e.dataTransfer.files));
    fileInput.addEventListener('change', function () { handleFiles(this.files); });

    function handleFiles(files) {
        if (!files.length) return;
        const file = files[0];
        if (!file.name.toLowerCase().endsWith('.csv')) {
            showToast('Please select a .csv file', 'error'); return;
        }
        const dt = new DataTransfer();
        dt.items.add(file);
        fileInput.files = dt.files;
        fileNameEl.textContent = file.name;
        fileInfo.classList.remove('hidden');
        submitBtn.removeAttribute('disabled');
    }

    if (removeBtn) {
        removeBtn.addEventListener('click', () => {
            fileInput.value = '';
            fileInfo.classList.add('hidden');
            submitBtn.setAttribute('disabled', 'true');
        });
    }
}


// ── Gallery ───────────────────────────────────────────────────
function initGallery() {
    const grid = document.getElementById('masonry-grid');
    if (!grid) return;

    // Stagger fade-in for cards
    const items = document.querySelectorAll('.masonry-item');
    items.forEach((item, i) => {
        setTimeout(() => item.classList.add('visible'), i * 40);
    });

    // Wire delete buttons
    document.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', function (e) {
            e.stopPropagation();
            const id = this.dataset.id;
            const card = document.getElementById(`item-${id}`);
            deleteItem(id, card);
        });
    });
}

function deleteItem(id, cardEl) {
    const formData = new FormData();
    formData.append('id', id);

    fetch('/gallery/delete/', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrftoken },
        body: formData
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === 'success') {
            cardEl.classList.add('removing');
            setTimeout(() => {
                cardEl.remove();
                updateCount();
                showToast('Removed from list', 'success');
            }, 400);
        } else {
            showToast('Error — could not remove', 'error');
        }
    })
    .catch(() => showToast('Network error', 'error'));
}

function updateCount() {
    const count = document.querySelectorAll('.masonry-item').length;
    const badge = document.getElementById('image-count');
    if (badge) badge.textContent = `${count} pins`;
}


// ── Toast ─────────────────────────────────────────────────────
function showToast(msg, type = 'success') {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const icon = type === 'success' ? 'fa-check-circle' : 'fa-circle-exclamation';
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `<i class="fa-solid ${icon}"></i><span>${msg}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.style.animation = 'slideIn .3s reverse forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}


// ── Download Images Modal (polling-based) ─────────────────────
let pollTimer = null;

function openDownloadModal() {
    showPhase('download');
    document.getElementById('dl-label').textContent = 'Starting download…';
    document.getElementById('dl-count').textContent  = '0 / 0';
    document.getElementById('dl-percent').textContent = '0%';
    document.getElementById('progress-fill').style.width = '0%';
    document.getElementById('dl-failed').style.display = 'none';
    document.getElementById('download-modal').style.display = 'flex';

    clearInterval(pollTimer);

    // Step 1: POST to start the job
    fetch('/gallery/start-download/', {
        method: 'POST',
        headers: { 'X-CSRFToken': csrftoken },
    })
    .then(r => r.json())
    .then(data => {
        if (data.status !== 'ok') {
            showModalError(data.message || 'Could not start download.');
            return;
        }
        const jobId = data.job_id;

        // Step 2: Poll status every 800 ms
        pollTimer = setInterval(() => {
            fetch(`/gallery/download-status/?job_id=${jobId}`)
            .then(r => r.json())
            .then(job => {
                if (job.status === 'downloading' || job.status === 'starting') {
                    const pct = job.percent || 0;
                    document.getElementById('progress-fill').style.width = pct + '%';
                    document.getElementById('dl-percent').textContent = pct + '%';
                    document.getElementById('dl-count').textContent = `${job.done} / ${job.total}`;
                    document.getElementById('dl-label').textContent =
                        `Downloading image ${job.done} of ${job.total}…`;
                    if (job.failed > 0) {
                        const el = document.getElementById('dl-failed');
                        el.style.display = 'block';
                        el.textContent = `${job.failed} image(s) could not be downloaded`;
                    }
                } else if (job.status === 'zipping') {
                    showPhase('zip');
                } else if (job.status === 'done') {
                    clearInterval(pollTimer);
                    const summary =
                        `${job.downloaded} image(s) downloaded` +
                        (job.failed > 0 ? `, ${job.failed} failed` : '') +
                        `. Zipped in ${job.zip_time}s.`;
                    document.getElementById('done-summary').textContent = summary;
                    // Wire the download button with job_id
                    document.getElementById('btn-get-zip').href = `/gallery/get-zip/?job_id=${jobId}`;
                    showPhase('done');
                } else if (job.status === 'error') {
                    clearInterval(pollTimer);
                    showModalError(job.message || 'An unknown error occurred.');
                }
            })
            .catch(() => {
                clearInterval(pollTimer);
                showModalError('Connection lost. Please try again.');
            });
        }, 800);
    })
    .catch(() => showModalError('Could not connect to server.'));
}

function showModalError(msg) {
    document.getElementById('error-msg').textContent = msg;
    showPhase('error');
}

function showPhase(name) {
    ['download', 'zip', 'done', 'error'].forEach(p => {
        document.getElementById(`modal-phase-${p}`).style.display =
            p === name ? 'block' : 'none';
    });
}

function closeDownloadModal() {
    document.getElementById('download-modal').style.display = 'none';
    clearInterval(pollTimer);
}

// Close on backdrop click
document.addEventListener('DOMContentLoaded', () => {
    const backdrop = document.getElementById('download-modal');
    if (backdrop) {
        backdrop.addEventListener('click', e => {
            if (e.target === backdrop) closeDownloadModal();
        });
    }
});


// ── Boot ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    initUpload();
    initGallery();
});
