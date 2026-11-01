'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { articles, type Article } from '@/lib/api';
import { sameSupplier } from '@/lib/requestPricing';

interface Props {
  description: string;
  vendorName: string;
  selected?: { articleNumber: string; supplierName: string };
  onChoose: (article: Article) => void;
}

export function ArticleSuggestions({ description, vendorName, selected, onChoose }: Props) {
  const trimmed = description.trim().slice(0, 250);
  const [search, setSearch] = useState(trimmed);
  const [expanded, setExpanded] = useState(true);
  const [data, setData] = useState<Article[]>([]);
  const [dataSearch, setDataSearch] = useState('');
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  useEffect(() => {
    const timeout = window.setTimeout(() => setSearch(trimmed), 500);
    return () => window.clearTimeout(timeout);
  }, [trimmed]);

  useEffect(() => {
    if (search.length < 3 || selected) return;
    let cancelled = false;
    setStatus('loading');
    articles.suggest(search).then(result => {
      if (!cancelled) { setData(result); setDataSearch(search); setStatus('ready'); }
    }).catch(() => { if (!cancelled) setStatus('error'); });
    return () => { cancelled = true; };
  }, [search, selected]);
  if (selected) return <p className="mt-3 text-xs text-accent-deep">Catalog article {selected.articleNumber} · {selected.supplierName} · review quantity and terms</p>;
  if (trimmed.length < 3 || search !== trimmed) return null;
  if (status === 'error') return <p className="mt-3 text-xs text-ink/60">Catalog suggestions unavailable. You can continue editing this line.</p>;
  if (status === 'idle' || status === 'loading' || dataSearch !== search) return <p className="mt-3 text-xs text-ink/50">Checking your catalog…</p>;
  if (!data?.length) return null;

  return <div className="mt-3 rounded-lg border border-accent/30 bg-accent-soft/20 p-3">
    <button type="button" onClick={() => setExpanded(!expanded)} aria-expanded={expanded} className="flex w-full items-center justify-between gap-2 text-left text-xs font-semibold text-accent-deep focus-visible:outline-2 focus-visible:outline-accent-deep">
      <span>Catalog matches ({data.length})</span><span aria-hidden="true">{expanded ? '−' : '+'}</span>
    </button>
    {expanded && <ul className="mt-2 divide-y divide-accent/20">
      {data.map(article => {
        const differentSupplier = Boolean(vendorName.trim() && article.supplier_name && !sameSupplier(vendorName, article.supplier_name));
        const unavailable = differentSupplier || !article.supplier_name || article.currency !== 'EUR';
        return <li key={article.id} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2 py-2 first:pt-0 last:pb-0">
          <div className="min-w-0 flex-1 text-xs leading-relaxed text-ink/70">
            <Link href={`/dashboard/articles/${article.id}`} target="_blank" rel="noopener noreferrer" className="font-medium text-accent-deep underline decoration-accent/60 underline-offset-2 hover:decoration-accent-deep focus-visible:outline-2 focus-visible:outline-accent-deep">
              {article.description} <span className="text-ink/55">↗<span className="sr-only"> (open article in a new tab)</span></span>
            </Link>
            <p>{article.supplier_name || 'Unknown supplier'} · {article.article_number} · catalog quantity {article.quantity}</p>
            {differentSupplier && <p className="text-ink/60">Different supplier—use a separate request or change the request supplier.</p>}
            {article.currency !== 'EUR' && <p className="text-ink/60">Only EUR prices can be applied here.</p>}
          </div>
          <div className="flex shrink-0 items-center gap-3"><span className="text-xs font-semibold tabular-nums text-ink">{article.currency} {Number(article.unit_price).toFixed(2)} / {article.unit}</span><button type="button" disabled={unavailable} onClick={() => onChoose(article)} className="rounded-full border border-line bg-white px-3 py-1.5 text-xs font-medium text-ink hover:bg-ink/5 focus-visible:outline-2 focus-visible:outline-accent-deep disabled:cursor-not-allowed disabled:opacity-40">Use article</button></div>
        </li>;
      })}
    </ul>}
  </div>;
}
