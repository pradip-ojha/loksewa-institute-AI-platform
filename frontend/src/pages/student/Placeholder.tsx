interface Props {
  title: string;
}

export function StudentPlaceholder({ title }: Props) {
  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold text-gray-900">{title}</h2>
      <div className="flex h-48 items-center justify-center rounded-xl border-2 border-dashed border-gray-200 bg-white">
        <p className="text-sm text-gray-400">Coming soon</p>
      </div>
    </div>
  );
}
