// ── Design conventions (enterprise light theme) ──────────────────────────────
// Brand: one deep blue. Primary action = brand-600; links/icons/active = brand-500/700.
// Neutrals: `gray-*` only (aliased to slate in tailwind.config.js). Never `slate-*`.
// Surfaces: white + `border border-gray-200` (1px). Shadow only on modals/toasts/menus (shadow-pop).
// Radius: controls `rounded-md`, cards/tables/modals `rounded-lg`, badges `rounded-full`.
// Type scale:
//   page title   → text-lg font-semibold tracking-tight text-gray-900   (PageHeader)
//   description  → text-sm text-gray-500
//   section/card → text-sm font-semibold text-gray-900
//   body         → text-sm text-gray-700 (secondary: text-gray-500)
//   meta/caption → text-xs text-gray-500
//   table header → text-xs font-medium text-gray-500 (sentence case)
//   stat value   → text-2xl font-semibold tracking-tight tabular-nums
//   numbers      → add `tabular-nums`
// Focus: fields `focus:border-brand-600 focus:ring-2 focus:ring-brand-600/20`.
export { cn } from "./cn";
export { Button } from "./Button";
export type { ButtonProps } from "./Button";
export { Card, CardHeader } from "./Card";
export { StatCard } from "./StatCard";
export { Modal } from "./Modal";
export { FormField, TextInput, Textarea, Select } from "./Form";
export { Badge, StatusBadge } from "./Badge";
export { Tabs } from "./Tabs";
export type { TabItem } from "./Tabs";
export { Spinner, PageLoader, EmptyState, Skeleton, Alert } from "./Feedback";
export { ToastProvider, useToast } from "./Toast";
export { PageHeader } from "./PageHeader";
export { DataTable } from "./DataTable";
export type { Column, DataTableProps } from "./DataTable";
export { SearchInput } from "./SearchInput";
export type { SearchInputProps } from "./SearchInput";
export { ConfirmDialog } from "./ConfirmDialog";
export type { ConfirmDialogProps } from "./ConfirmDialog";
export { Menu } from "./Menu";
export type { MenuItem } from "./Menu";
export { Pagination } from "./Pagination";
export type { PaginationProps } from "./Pagination";
