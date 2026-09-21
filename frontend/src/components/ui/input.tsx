import { forwardRef, type InputHTMLAttributes } from 'react';
import { cn } from '../../lib/utils';

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn('h-12 w-full rounded-2xl border border-[#E8C9A4] bg-[#FFF9F0] px-4 text-sm text-ink outline-none transition placeholder:text-[#B78F6C] focus:border-orange focus:ring-4 focus:ring-orange/10', className)} {...props} />
));
Input.displayName = 'Input';
