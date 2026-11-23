import { clsx, type ClassValue } from "clsx";

/** Join conditional class names. */
export function cn(...values: ClassValue[]): string {
  return clsx(values);
}
