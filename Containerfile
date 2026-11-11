# Dokumente (laib ADR 0028): paper and files into Paperless. The scan (laib #568): paper stacks into Paperless. SANE for a network or USB scanner (#801)
# (sane-airscan finds eSCL/AirScan devices for the setup's search, #571),
# Tesseract (deu + OSD) to turn and read each page, poppler and qpdf for a
# PDF that came in, Pillow and img2pdf for the PDFs of scanned pages, libzbar
# for the separator sheets' Code 128 / QR (#814: the library alone, scan.py
# calls it through ctypes; zbar-tools would pull in ImageMagick); the program
# itself is scan.py, stdlib apart from those.
FROM docker.io/library/debian:trixie-slim@sha256:a72b4433de1cf7d6bae0c4cee74d0547f58601a643b3375f070c51600d63b6b2

RUN apt-get update && apt-get install -y --no-install-recommends \
      sane-utils libsane1 sane-airscan tesseract-ocr tesseract-ocr-deu tesseract-ocr-osd \
      poppler-utils qpdf libzbar0t64 python3 python3-pil python3-img2pdf ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY scan/scan.py /opt/scan.py
COPY vertraege/vertraege.py /opt/vertraege.py
COPY ordnen/ordnen.py /opt/ordnen.py
COPY ordnen/doppelt.py /opt/doppelt.py
COPY ordnen/zuteilen.py /opt/zuteilen.py
COPY ordnen/berichtigen.py /opt/berichtigen.py
COPY ordnen/firmen.py /opt/firmen.py
COPY ordnen/kontakte_aufraeumen.py /opt/kontakte_aufraeumen.py
COPY ordnen/kontakte_nachlesen.py /opt/kontakte_nachlesen.py
COPY dienst.py /opt/dienst.py
LABEL org.opencontainers.image.source=https://github.com/mdopp/dokumente
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python3", "/opt/dienst.py"]
