/* Vuma Field PWA – offline queue, signature pad and device capture. */
(function () {
    'use strict';

    var QUEUE_KEY = 'vuma_field_queue_v1';
    var SYNC_INTERVAL = 60000;
    var syncing = false;

    function $(selector, root) {
        return (root || document).querySelector(selector);
    }

    function $all(selector, root) {
        return Array.prototype.slice.call((root || document).querySelectorAll(selector));
    }

    function uuid() {
        if (window.crypto && window.crypto.randomUUID) {
            return window.crypto.randomUUID();
        }
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
            var r = Math.random() * 16 | 0;
            var v = c === 'x' ? r : ((r & 0x3) | 0x8);
            return v.toString(16);
        });
    }

    function toast(message, isError) {
        var el = document.getElementById('toast');
        if (!el) { return; }
        el.textContent = message;
        el.className = 'toast show' + (isError ? ' error' : '');
        window.clearTimeout(toast._timer);
        toast._timer = window.setTimeout(function () {
            el.className = 'toast';
        }, 4200);
    }

    function readQueue() {
        try {
            return JSON.parse(window.localStorage.getItem(QUEUE_KEY) || '[]');
        } catch (err) {
            return [];
        }
    }

    function writeQueue(queue) {
        window.localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
        updateQueueBadge();
    }

    function enqueue(item) {
        var queue = readQueue();
        queue.push(item);
        writeQueue(queue);
    }

    function updateQueueBadge() {
        var queue = readQueue();
        var banner = document.getElementById('queue-banner');
        var count = document.getElementById('queue-count');
        if (count) { count.textContent = queue.length; }
        if (banner) { banner.hidden = queue.length === 0; }
    }

    function updateOnlineBadge() {
        var dot = document.getElementById('online-dot');
        if (!dot) { return; }
        dot.className = 'online-dot ' + (navigator.onLine ? 'online' : 'offline');
        dot.title = navigator.onLine ? 'Online' : 'Offline – actions are queued';
    }

    function fileToDataURL(file) {
        return new Promise(function (resolve, reject) {
            var reader = new FileReader();
            reader.onload = function () { resolve(reader.result); };
            reader.onerror = reject;
            reader.readAsDataURL(file);
        });
    }

    function formPayload(form) {
        var payload = {};
        var files = [];
        Array.prototype.forEach.call(form.elements, function (el) {
            if (!el.name || el.disabled) { return; }
            if (el.type === 'file') {
                if (el.files && el.files[0]) { files.push({ name: el.name, file: el.files[0] }); }
                return;
            }
            if (el.type === 'checkbox') {
                payload[el.name] = el.checked;
                return;
            }
            if (el.type === 'radio') {
                if (el.checked) { payload[el.name] = el.value; }
                return;
            }
            payload[el.name] = el.value;
        });
        var reads = files.map(function (item) {
            return fileToDataURL(item.file).then(function (data) {
                payload[item.name] = data;
            });
        });
        return Promise.all(reads).then(function () { return payload; });
    }

    function postJSON(url, data) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            },
            body: JSON.stringify(data)
        }).then(function (response) {
            return response.json().catch(function () { return {}; }).then(function (body) {
                if (!response.ok) {
                    throw new Error(body.error || ('Server returned ' + response.status));
                }
                return body;
            });
        });
    }

    function submitForm(form) {
        var type = form.getAttribute('data-offline-type') || 'action';
        var endpoint = form.getAttribute('data-endpoint');
        var submit = form.querySelector('button[type="submit"]');
        if (submit) { submit.disabled = true; }
        return formPayload(form).then(function (payload) {
            var item = { uuid: uuid(), type: type, endpoint: endpoint, payload: payload };
            if (!navigator.onLine) {
                enqueue(item);
                toast('Saved offline. It will sync when you have signal.');
                window.setTimeout(function () { window.location.href = '/vuma/field'; }, 900);
                return;
            }
            return postJSON(endpoint, Object.assign({ client_uuid: item.uuid }, payload))
                .then(function (body) {
                    if (body && body.ok) {
                        toast('Saved.');
                        window.setTimeout(function () { window.location.href = '/vuma/field'; }, 700);
                    } else {
                        throw new Error((body && body.error) || 'Could not save.');
                    }
                })
                .catch(function (err) {
                    enqueue(item);
                    toast('Network problem – saved offline. ' + (err.message || ''), true);
                    window.setTimeout(function () { window.location.href = '/vuma/field'; }, 1200);
                });
        }).catch(function (err) {
            toast(err.message || 'Could not read the form.', true);
        }).then(function () {
            if (submit) { submit.disabled = false; }
        });
    }

    function syncQueue() {
        if (syncing) { return Promise.resolve(); }
        var queue = readQueue();
        if (!queue.length) {
            updateQueueBadge();
            return Promise.resolve();
        }
        if (!navigator.onLine) {
            toast('Still offline – ' + queue.length + ' action(s) waiting.', true);
            return Promise.resolve();
        }
        syncing = true;
        var actions = queue.map(function (item) {
            return { uuid: item.uuid, type: item.type, payload: item.payload };
        });
        return postJSON('/vuma/field/sync', { actions: actions }).then(function (body) {
            var remaining = [];
            var succeeded = 0;
            var byUuid = {};
            (body.results || []).forEach(function (result) { byUuid[result.uuid] = result; });
            queue.forEach(function (item) {
                var result = byUuid[item.uuid];
                if (result && result.ok) {
                    succeeded += 1;
                } else {
                    remaining.push(item);
                }
            });
            writeQueue(remaining);
            if (succeeded) {
                toast(succeeded + ' action(s) synced.');
            }
            if (remaining.length) {
                toast(remaining.length + ' action(s) still need attention.', true);
            }
        }).catch(function (err) {
            toast('Sync failed: ' + (err.message || 'network error'), true);
        }).then(function () {
            syncing = false;
        });
    }

    function setupSignaturePads() {
        $all('[data-signature-pad]').forEach(function (canvas) {
            var form = canvas.closest('form');
            var hidden = form ? form.querySelector('[data-signature-value]') : null;
            var ctx = canvas.getContext('2d');
            var drawing = false;
            var hasInk = false;
            ctx.lineWidth = 3;
            ctx.lineCap = 'round';
            ctx.lineJoin = 'round';
            ctx.strokeStyle = '#10231b';

            function position(event) {
                var rect = canvas.getBoundingClientRect();
                var point = event.touches ? event.touches[0] : event;
                return {
                    x: (point.clientX - rect.left) * (canvas.width / rect.width),
                    y: (point.clientY - rect.top) * (canvas.height / rect.height)
                };
            }

            function start(event) {
                event.preventDefault();
                drawing = true;
                hasInk = true;
                var p = position(event);
                ctx.beginPath();
                ctx.moveTo(p.x, p.y);
            }

            function move(event) {
                if (!drawing) { return; }
                event.preventDefault();
                var p = position(event);
                ctx.lineTo(p.x, p.y);
                ctx.stroke();
            }

            function end(event) {
                if (!drawing) { return; }
                if (event) { event.preventDefault(); }
                drawing = false;
                if (hidden) { hidden.value = canvas.toDataURL('image/png'); }
            }

            canvas.addEventListener('mousedown', start);
            canvas.addEventListener('mousemove', move);
            window.addEventListener('mouseup', end);
            canvas.addEventListener('touchstart', start, { passive: false });
            canvas.addEventListener('touchmove', move, { passive: false });
            canvas.addEventListener('touchend', end, { passive: false });

            var clearButton = form ? form.querySelector('[data-clear-signature]') : null;
            if (clearButton) {
                clearButton.addEventListener('click', function () {
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
                    hasInk = false;
                    if (hidden) { hidden.value = ''; }
                });
            }

            if (form) {
                form.addEventListener('submit', function (event) {
                    if (!hasInk) {
                        event.preventDefault();
                        event.stopImmediatePropagation();
                        toast('Please capture the customer signature.', true);
                        return;
                    }
                    if (hidden) { hidden.value = canvas.toDataURL('image/png'); }
                }, true);
            }
        });
    }

    function setupGeolocation() {
        var lat = document.querySelector('[data-geo="latitude"]');
        var lon = document.querySelector('[data-geo="longitude"]');
        if ((!lat && !lon) || !navigator.geolocation) { return; }
        navigator.geolocation.getCurrentPosition(function (position) {
            if (lat) { lat.value = position.coords.latitude; }
            if (lon) { lon.value = position.coords.longitude; }
        }, function () {
            /* Location is useful but not mandatory; never block a POD. */
        }, { enableHighAccuracy: true, timeout: 8000, maximumAge: 30000 });
    }

    function setupForms() {
        $all('form[data-field-form]').forEach(function (form) {
            form.addEventListener('submit', function (event) {
                event.preventDefault();
                submitForm(form);
            });
        });
    }

    function setupTripActions() {
        $all('[data-trip-action]').forEach(function (button) {
            button.addEventListener('click', function () {
                var tripId = button.getAttribute('data-trip-id');
                var action = button.getAttribute('data-trip-action');
                button.disabled = true;
                postJSON('/vuma/field/trip/' + tripId + '/' + action, {})
                    .then(function () {
                        toast('Trip updated.');
                        window.location.reload();
                    })
                    .catch(function (err) {
                        toast(err.message || 'Could not update trip.', true);
                        button.disabled = false;
                    });
            });
        });
    }

    function setupServiceWorker() {
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.register('/vuma/field/sw.js', { scope: '/vuma/field/' })
                .catch(function () { /* PWA install still works without SW. */ });
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        setupSignaturePads();
        setupGeolocation();
        setupForms();
        setupTripActions();
        setupServiceWorker();
        updateQueueBadge();
        updateOnlineBadge();

        var syncButton = document.getElementById('sync-now');
        if (syncButton) {
            syncButton.addEventListener('click', syncQueue);
        }
        window.addEventListener('online', function () {
            updateOnlineBadge();
            syncQueue();
        });
        window.addEventListener('offline', updateOnlineBadge);
        window.setInterval(function () {
            if (navigator.onLine) { syncQueue(); }
        }, SYNC_INTERVAL);
    });
})();
