import { type ButtonHTMLAttributes, forwardRef } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '../../lib/utils';

const buttonVariants = cva(
  'inline-flex items-center justify-center gap-2 rounded-2xl text-sm font-extrabold transition-all focus:outline-none focus:ring-4 focus:ring-orange/20 disabled:pointer-events-none disabled:opacity-50',
  {
    variants: {
      variant: {
        default: 'bg-orange text-white shadow-[0_10px_0_#C94F12] hover:-translate-y-0.5 hover:shadow-[0_12px_0_#C94F12] active:translate-y-1 active:shadow-[0_4px_0_#C94F12]',
        secondary: 'bg-[#FFE0BB] text-ink hover:bg-[#FFD1A1]',
        ghost: 'text-[#87512F] hover:bg-[#FFE7CB]',
      },
      size: { default: 'h-12 px-5', sm: 'h-9 px-3 text-xs', lg: 'h-14 px-7 text-base' },
    },
    defaultVariants: { variant: 'default', size: 'default' },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, variant, size, ...props }, ref) => (
  <button ref={ref} className={cn(buttonVariants({ variant, size }), className)} {...props} />
));
Button.displayName = 'Button';
