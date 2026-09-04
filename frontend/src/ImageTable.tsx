import type { ImageResult } from "./api";

interface Props {
  images: ImageResult[];
  onSelect: (image: ImageResult) => void;
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "Unknown";
  if (bytes < 1024) return bytes + " B";
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; value >= 1024 && index < units.length; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return (value >= 10 ? value.toFixed(1) : value.toFixed(2)) + " " + unit;
}

function formatDate(timestamp: number): string {
  if (!timestamp) return "—";
  const date = new Date(timestamp * 1000);
  return Number.isNaN(date.getTime())
    ? "—"
    : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function ImageTable({ images, onSelect }: Props) {
  return (
    <div className="image-table-wrap">
      <table className="image-table">
        <thead>
          <tr>
            <th scope="col" className="col-thumb" aria-label="Preview" />
            <th scope="col">Name</th>
            <th scope="col">Type</th>
            <th scope="col">Dimensions</th>
            <th scope="col">Size</th>
            <th scope="col">Added</th>
            <th scope="col">Objects</th>
          </tr>
        </thead>
        <tbody>
          {images.map((image) => {
            const filename = image.path.split(/[\\/]/).pop() ?? image.path;
            const type =
              (image.format || filename.split(".").pop() || "").toUpperCase() || "—";
            return (
              <tr key={image.id} onClick={() => onSelect(image)}>
                <td className="col-thumb">
                  <img src={image.thumbnail_url} alt="" loading="lazy" />
                </td>
                <td>
                  <button
                    type="button"
                    className="table-name"
                    title={image.path}
                    onClick={(event) => {
                      event.stopPropagation();
                      onSelect(image);
                    }}
                  >
                    {filename}
                  </button>
                </td>
                <td>{type}</td>
                <td>
                  {image.width > 0 && image.height > 0
                    ? `${image.width} × ${image.height}`
                    : "—"}
                </td>
                <td>{formatBytes(image.size)}</td>
                <td>{formatDate(image.indexed_at)}</td>
                <td className="col-objects" title={image.objects.join(", ")}>
                  {image.objects.length > 0 ? image.objects.join(", ") : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
