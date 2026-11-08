interface Props {
  page: number;
  pageCount: number;
  onChange: (page: number) => void;
}

type PageItem = number | "ellipsis-start" | "ellipsis-end";

function visiblePages(page: number, pageCount: number): PageItem[] {
  if (pageCount <= 7) {
    return Array.from({ length: pageCount }, (_, index) => index + 1);
  }

  const pages = new Set([1, pageCount, page - 1, page, page + 1]);
  const ordered = [...pages].filter((item) => item >= 1 && item <= pageCount).sort((a, b) => a - b);
  const result: PageItem[] = [];
  ordered.forEach((item, index) => {
    const previous = ordered[index - 1];
    if (previous !== undefined && item - previous > 1) {
      result.push(previous === 1 ? "ellipsis-start" : "ellipsis-end");
    }
    result.push(item);
  });
  return result;
}

export function Pagination({ page, pageCount, onChange }: Props) {
  if (pageCount <= 1) return null;

  return (
    <nav className="pagination" aria-label="Search result pages">
      <button type="button" className="btn-ghost" onClick={() => onChange(page - 1)} disabled={page === 1}>
        Previous
      </button>
      <div className="page-numbers">
        {visiblePages(page, pageCount).map((item) =>
          typeof item === "number" ? (
            <button
              type="button"
              key={item}
              className={item === page ? "current-page" : undefined}
              aria-label={`Page ${item}`}
              aria-current={item === page ? "page" : undefined}
              onClick={() => onChange(item)}
            >
              {item}
            </button>
          ) : (
            <span key={item} aria-hidden="true">…</span>
          ),
        )}
      </div>
      <button type="button" className="btn-ghost" onClick={() => onChange(page + 1)} disabled={page === pageCount}>
        Next
      </button>
    </nav>
  );
}
