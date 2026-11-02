'use client';

import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import type { QuoteRegion } from '@/lib/api';

interface Props {
  url: string;
  regions: QuoteRegion[];
  activeField: string | null;
  onActiveField: (field: string | null) => void;
  onSelectField: (field: string) => void;
}

function QuotePage({ pdf, number, width, regions, activeField, onActiveField, onSelectField, interacting }: {
  pdf: PDFDocumentProxy; number: number; width: number; regions: QuoteRegion[]; activeField: string | null;
  onActiveField: Props['onActiveField']; onSelectField: Props['onSelectField']; interacting: React.MutableRefObject<boolean>;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [height, setHeight] = useState(0);
  useEffect(() => {
    if (!width || !canvas.current) return;
    let cancelled = false;
    let cancelRender: (() => void) | undefined;
    (async () => {
      try {
        const page = await pdf.getPage(number);
        if (cancelled || !canvas.current) return;
        const viewport = page.getViewport({ scale: width / page.getViewport({ scale: 1 }).width });
        const ratio = Math.min(window.devicePixelRatio || 1, 1.5);
        canvas.current.width = Math.round(viewport.width * ratio);
        canvas.current.height = Math.round(viewport.height * ratio);
        setHeight(viewport.height);
        const context = canvas.current.getContext('2d');
        if (!context) return;
        const task = page.render({ canvasContext: context, viewport, transform: [ratio, 0, 0, ratio, 0, 0], annotationMode: 0 });
        cancelRender = () => task.cancel();
        await task.promise;
      } catch (error) {
        if (!cancelled && !(error instanceof Error && error.name === 'RenderingCancelledException')) console.error('Unable to render quote page', error);
      }
    })();
    return () => { cancelled = true; cancelRender?.(); };
  }, [pdf, number, width]);

  return <div data-pdf-page={number} className="relative mx-auto w-full max-w-[850px] bg-white shadow-sm" style={{ minHeight: height || width * 1.414 }}>
    <canvas ref={canvas} className="block h-auto w-full" aria-label={`Quote page ${number}`} />
    {height > 0 && regions.filter(region => region.page === number).map((region, index) => <button
      key={`${region.field}-${index}`} type="button" aria-label={`${region.matchKind === 'price_row' ? 'Possible item row inferred from price and quantity' : 'Possible quote match'}: ${region.field}`}
      onMouseEnter={() => { interacting.current = true; onActiveField(region.field); }}
      onMouseLeave={() => { interacting.current = false; onActiveField(null); }}
      onFocus={() => { interacting.current = true; onActiveField(region.field); }}
      onBlur={() => { interacting.current = false; onActiveField(null); }}
      onClick={() => onSelectField(region.field)}
      className={`absolute rounded-sm transition-colors focus:outline-2 focus:outline-offset-1 focus:outline-accent-deep ${activeField === region.field ? 'bg-accent-deep/45 ring-2 ring-accent-deep' : region.category === 'vendor' ? 'bg-[#55b5cb]/35 hover:bg-[#55b5cb]/60' : 'bg-[#efbb52]/40 hover:bg-[#efbb52]/70'}`}
      style={{ left: `${region.x * 100}%`, top: `${region.y * 100}%`, width: `${region.width * 100}%`, height: `${region.height * 100}%` }} />)}
  </div>;
}

export function QuoteViewer({ url, regions, activeField, onActiveField, onSelectField }: Props) {
  const [pdf, setPdf] = useState<PDFDocumentProxy | null>(null);
  const [width, setWidth] = useState(0);
  const [error, setError] = useState(false);
  const [loading, setLoading] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const interacting = useRef(false);

  useEffect(() => {
    let cancelled = false;
    let destroy: (() => void) | undefined;
    setLoading(true); setError(false); setPdf(null);
    (async () => {
      try {
        const pdfjs = await import('pdfjs-dist');
        if (cancelled) return;
        pdfjs.GlobalWorkerOptions.workerSrc = new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url).toString();
        const task = pdfjs.getDocument(url);
        destroy = () => { void task.destroy().catch(() => {}); };
        const document = await task.promise;
        if (!cancelled) setPdf(document);
      } catch { if (!cancelled) setError(true); }
      finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; destroy?.(); };
  }, [url]);

  useEffect(() => {
    const container = contentRef.current;
    if (!container) return;
    const observer = new ResizeObserver(entries => setWidth(Math.min(entries[0]?.contentRect.width ?? 0, 850)));
    observer.observe(container);
    return () => observer.disconnect();
  }, [error, loading]);

  useEffect(() => {
    if (!activeField || interacting.current) return;
    const region = regions.find(item => item.field === activeField);
    const scroll = scrollRef.current;
    const page = scroll?.querySelector<HTMLElement>(`[data-pdf-page="${region?.page}"]`);
    if (!region || !scroll || !page) return;
    const top = page.getBoundingClientRect().top - scroll.getBoundingClientRect().top + scroll.scrollTop + region.y * page.clientHeight;
    if (top < scroll.scrollTop || top + region.height * page.clientHeight > scroll.scrollTop + scroll.clientHeight) {
      scroll.scrollTo({ top: Math.max(0, top - scroll.clientHeight / 3), behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
    }
  }, [activeField, regions, pdf, width]);

  if (error) return <div><p className="px-4 py-2 text-sm text-ink/65">Interactive view unavailable. Showing the PDF in your browser instead.</p><iframe title="Vendor quote preview" src={url} className="h-[65vh] min-h-96 w-full bg-gray-100 xl:h-[calc(100vh-10rem)]" /></div>;

  return <div className="border-t border-line bg-[#eef1f3]">
    {loading && <p className="p-8 text-center text-sm text-ink/60">Loading quote…</p>}
    <div ref={scrollRef} className="h-[65vh] min-h-80 overflow-auto p-2 xl:h-[calc(100vh-10rem)]" aria-label="Scrollable supplier quote">
      <div ref={contentRef} className="mx-auto max-w-[850px] space-y-2">
        {pdf && Array.from({ length: pdf.numPages }, (_, i) => <QuotePage key={i + 1} pdf={pdf} number={i + 1} width={width} regions={regions} activeField={activeField} onActiveField={onActiveField} onSelectField={onSelectField} interacting={interacting} />)}
      </div>
    </div>
  </div>;
}
