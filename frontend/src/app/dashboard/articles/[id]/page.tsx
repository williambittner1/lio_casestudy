'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { Alert } from '@/components/base';
import { articles, type Article } from '@/lib/api';

export default function ArticleDetailPage() {
  const { id } = useParams<{ id: string }>();
  const [article, setArticle] = useState<Article | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    articles.get(id).then(value => {
      if (!cancelled) setArticle(value);
    }).catch(err => {
      if (!cancelled) setError(err instanceof Error ? err.message : 'Could not load this article.');
    });
    return () => { cancelled = true; };
  }, [id]);

  return <div className="mx-auto max-w-4xl px-4 py-8 sm:px-6 lg:px-8">
    <Link href="/dashboard/articles" className="text-sm font-medium text-accent-deep underline underline-offset-2">
      ← All articles
    </Link>
    {error ? <Alert variant="error" className="mt-6">{error}</Alert> : !article
      ? <p className="mt-8 text-sm text-ink/55" role="status">Loading article…</p>
      : <div className="lio-card mt-6 p-6 sm:p-8">
        <p className="font-mono text-sm text-ink/55">{article.article_number}</p>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">{article.description}</h1>
        <dl className="mt-7 grid grid-cols-1 gap-6 border-t border-line pt-6 sm:grid-cols-2">
          <div><dt className="text-sm text-ink/55">Supplier</dt><dd className="mt-1 font-medium text-ink">{article.supplier_name || '—'}</dd></div>
          <div><dt className="text-sm text-ink/55">Unit price</dt><dd className="mt-1 font-semibold tabular-nums text-ink">{article.currency} {Number(article.unit_price).toFixed(2)} / {article.unit}</dd></div>
          <div><dt className="text-sm text-ink/55">Catalog quantity</dt><dd className="mt-1 tabular-nums text-ink">{Number(article.quantity).toString()}</dd></div>
          <div><dt className="text-sm text-ink/55">Unit</dt><dd className="mt-1 text-ink">{article.unit}</dd></div>
        </dl>
      </div>}
  </div>;
}
