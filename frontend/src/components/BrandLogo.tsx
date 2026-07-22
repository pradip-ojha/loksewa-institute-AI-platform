const IMG_SIZES = { sm: "h-6 w-6", md: "h-7 w-7", lg: "h-11 w-11" } as const;
const NAME_SIZES = { sm: "text-sm", md: "text-sm", lg: "text-xl" } as const;

interface BrandLogoProps {
  size?: keyof typeof IMG_SIZES;
  withName?: boolean;
  className?: string;
}

/** Company logo mark, optionally with the product name. Used in layouts + login. */
export function BrandLogo({ size = "md", withName = false, className = "" }: BrandLogoProps) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <img src="/logo.png" alt="NeuraFix Loksewa" className={`${IMG_SIZES[size]} object-contain`} />
      {withName && (
        <span className={`${NAME_SIZES[size]} font-semibold tracking-tight text-gray-900`}>
          NeuraFix <span className="text-brand-600">Loksewa</span>
        </span>
      )}
    </span>
  );
}
