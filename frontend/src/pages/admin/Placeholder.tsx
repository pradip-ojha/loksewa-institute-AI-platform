interface Props {
  title: string;
  description?: string;
}

export function AdminPlaceholder({ title, description }: Props) {
  return (
    <div>
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-gray-900">{title}</h2>
        {description && <p className="mt-1 text-sm text-gray-500">{description}</p>}
      </div>
      <div className="flex h-64 items-center justify-center rounded-xl border-2 border-dashed border-gray-200 bg-white">
        <p className="text-sm text-gray-400">Coming in a future stage</p>
      </div>
    </div>
  );
}
