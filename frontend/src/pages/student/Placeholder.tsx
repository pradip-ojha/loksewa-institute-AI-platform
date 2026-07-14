import { EmptyState } from "../../components/ui";

interface Props {
  title: string;
}

export function StudentPlaceholder({ title }: Props) {
  return (
    <div>
      <h2 className="mb-4 text-lg font-semibold tracking-tight text-gray-900">{title}</h2>
      <EmptyState title="Coming soon" description="This section is not available yet." />
    </div>
  );
}
