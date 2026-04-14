import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import pdfWorker from "react-pdf/node_modules/pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

interface PdfScrollViewerProps {
  file: string;
  title: string;
  onLoad: () => void;
  onReachEnd: () => void;
}

export function PdfScrollViewer({ file, title, onLoad, onReachEnd }: PdfScrollViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [numPages, setNumPages] = useState(0);
  const [pageWidth, setPageWidth] = useState(720);
  const [hasReachedEnd, setHasReachedEnd] = useState(false);
  const isMobile = typeof window !== "undefined" && window.matchMedia("(max-width: 767.98px)").matches;

  useEffect(() => {
    setNumPages(0);
    setHasReachedEnd(false);
    if (containerRef.current) {
      containerRef.current.scrollTop = 0;
      containerRef.current.scrollLeft = 0;
    }
  }, [file]);

  useEffect(() => {
    const current = containerRef.current;
    if (!current) return;

    const observer = new ResizeObserver((entries) => {
      const horizontalPadding = isMobile ? 4 : 24;
      const nextWidth = Math.floor(entries[0].contentRect.width - horizontalPadding);
      setPageWidth(Math.max(isMobile ? 260 : 280, Math.min(nextWidth, isMobile ? 640 : 820)));
    });

    observer.observe(current);
    return () => observer.disconnect();
  }, []);


  useLayoutEffect(() => {
    if (!isMobile || !containerRef.current) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      if (containerRef.current) {
        containerRef.current.scrollLeft = 0;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [isMobile, pageWidth, numPages]);

  const handleScroll = () => {
    const current = containerRef.current;
    if (!current || hasReachedEnd) return;

    const reachedEnd = current.scrollTop + current.clientHeight >= current.scrollHeight - 24;

    if (reachedEnd) {
      setHasReachedEnd(true);
      onReachEnd();
    }
  };

  return (
    <div className="pdf-viewer">
      <div className="pdf-viewer__hint">
        Recorre el documento completo hasta el final para habilitar la aceptacion.
      </div>
      <div ref={containerRef} className={`pdf-viewer__scroll ${isMobile ? "pdf-viewer__scroll--mobile" : ""}`} onScroll={handleScroll}>
        <Document
          file={file}
          loading={<div className="pdf-viewer__state">Cargando PDF...</div>}
          error={<div className="pdf-viewer__state">No fue posible mostrar el PDF.</div>}
          onLoadSuccess={({ numPages: totalPages }) => {
            setNumPages(totalPages);
            if (containerRef.current) {
              containerRef.current.scrollTop = 0;
              containerRef.current.scrollLeft = 0;
            }
            onLoad();
          }}
        >
          <div className="pdf-viewer__pages">
            {Array.from({ length: numPages }, (_, index) => (
              <Page
                key={`${title}-${index + 1}`}
                pageNumber={index + 1}
                width={pageWidth}
                renderAnnotationLayer
                renderTextLayer
              />
            ))}
          </div>
        </Document>
      </div>
    </div>
  );
}
