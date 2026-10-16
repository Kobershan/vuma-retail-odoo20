#!/usr/bin/env python3
"""Minimal SMTP sink - accepts mail from Odoo and writes it to disk.

Lets us exercise Odoo's REAL smtp path (EHLO/MAIL/RCPT/DATA) and inspect the
exact message and attachments it produces, with no external SMTP account.

    python3 /tmp/opencode/smtp_sink.py [port] [outdir]
"""
import email
import os
import socket
import socketserver
import sys
import threading
from datetime import datetime

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 1025
OUTDIR = sys.argv[2] if len(sys.argv) > 2 else '/tmp/opencode/maildrop'
os.makedirs(OUTDIR, exist_ok=True)

counter = [0]
lock = threading.Lock()


class SMTPHandler(socketserver.StreamRequestHandler):
    """Speaks just enough SMTP for Odoo's mail sending to succeed."""

    def reply(self, text):
        self.wfile.write((text + '\r\n').encode())
        self.wfile.flush()

    def handle(self):
        self.reply('220 vuma-retail-smtp-sink ESMTP ready')
        sender = ''
        rcpts = []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            up = line.upper()

            if up.startswith('EHLO'):
                self.reply('250-vuma-retail-smtp-sink')
                self.reply('250-8BITMIME')
                self.reply('250-SMTPUTF8')
                # Odoo's ir.mail_server only offers login/certificate/cli auth,
                # so the sink must advertise AUTH or the send fails with
                # "SMTP AUTH extension not supported by server".
                self.reply('250-AUTH PLAIN LOGIN')
                self.reply('250 SIZE 104857600')
            elif up.startswith('HELO'):
                self.reply('250 vuma-retail-smtp-sink')
            elif up.startswith('AUTH'):
                # Accept any credentials; this is a sink, not a gate.
                self.reply('235 2.7.0 Authentication successful')
            elif up.startswith('MAIL FROM'):
                sender = line.split(':', 1)[1].strip() if ':' in line else ''
                self.reply('250 2.1.0 Sender OK')
            elif up.startswith('RCPT TO'):
                rcpts.append(line.split(':', 1)[1].strip() if ':' in line else '')
                self.reply('250 2.1.5 Recipient OK')
            elif up.startswith('DATA'):
                self.reply('354 End data with <CR><LF>.<CR><LF>')
                chunks = []
                while True:
                    buf = self.rfile.readline()
                    if not buf:
                        break
                    txt = buf.decode('utf-8', 'replace')
                    if txt.rstrip('\r\n') == '.':
                        break
                    # undo dot-stuffing
                    if txt.startswith('..'):
                        txt = txt[1:]
                    chunks.append(txt)
                self._store(''.join(chunks), sender, rcpts)
                self.reply('250 2.0.0 Ok: queued as TEST')
                sender, rcpts = '', []
            elif up.startswith('RSET'):
                sender, rcpts = '', []
                self.reply('250 2.0.0 Ok')
            elif up.startswith('NOOP'):
                self.reply('250 2.0.0 Ok')
            elif up.startswith('QUIT'):
                self.reply('221 2.0.0 Bye')
                return
            else:
                self.reply('250 2.0.0 Ok')

    def _store(self, raw, sender, rcpts):
        with lock:
            counter[0] += 1
            n = counter[0]
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        rawfile = os.path.join(OUTDIR, f'{stamp}-{n:03d}.eml')
        with open(rawfile, 'w', encoding='utf-8') as f:
            f.write(raw)
        msg = email.message_from_string(raw)
        with open(os.path.join(OUTDIR, 'index.txt'), 'a', encoding='utf-8') as f:
            subj = msg.get('Subject', '(no subject)')
            to = msg.get('To', '')
            f.write(f'{os.path.basename(rawfile)}\t{to}\t{subj}\n')
        print(f'[sink] captured #{n} to={to} subject={subj!r} '
              f'({len(raw)} bytes) -> {rawfile}', flush=True)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == '__main__':
    srv = Server(('127.0.0.1', PORT), SMTPHandler)
    print(f'[sink] listening on 127.0.0.1:{PORT}, writing to {OUTDIR}', flush=True)
    srv.serve_forever()
