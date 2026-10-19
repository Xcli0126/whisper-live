import wave, time, urllib.request, json

def post(path):
    data = open(path, 'rb').read()
    boundary = b'----X'
    body = (b'--' + boundary + b'\r\n'
            + b'Content-Disposition: form-data; name="file"; filename="a.wav"\r\n'
            + b'Content-Type: audio/wav\r\n\r\n' + data + b'\r\n--' + boundary + b'--\r\n')
    req = urllib.request.Request('http://127.0.0.1:8100/inference', data=body,
                                 headers={'Content-Type': 'multipart/form-data; boundary=----X'})
    t = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=120))
    return time.time() - t, r.get('text', '')

w = wave.open('test/sample3.wav'); sr = w.getframerate()
for secs in (2, 4, 8):
    w.setpos(0); frames = w.readframes(int(secs * sr))
    clip = f'test/clip{secs}.wav'
    o = wave.open(clip, 'wb'); o.setnchannels(1); o.setsampwidth(2); o.setframerate(sr)
    o.writeframes(frames); o.close()
    dt, text = post(clip)
    print(f'{secs}s audio -> {dt:.2f}s  |{text.strip()[:80]}')
dt, text = post('test/sample3.wav')
print(f'11.3s audio -> {dt:.2f}s  |{text.strip()[:90]}')
