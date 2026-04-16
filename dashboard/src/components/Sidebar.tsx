"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion } from "framer-motion";
import { useState } from "react";

const NAV_ITEMS = [
  {
    label: "Dashboard",
    href: "/",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="2" y="2" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
        <rect x="11" y="2" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
        <rect x="2" y="11" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
        <rect x="11" y="11" width="7" height="7" rx="1.5" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
  {
    label: "Containers",
    href: "/containers",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <rect x="3" y="4" width="14" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
        <rect x="3" y="11" width="14" height="5" rx="1" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="6" cy="6.5" r="1" fill="currentColor" />
        <circle cx="6" cy="13.5" r="1" fill="currentColor" />
      </svg>
    ),
  },
  {
    label: "Create",
    href: "/create",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <circle cx="10" cy="10" r="7.5" stroke="currentColor" strokeWidth="1.5" />
        <path d="M10 7V13M7 10H13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    label: "System",
    href: "/system",
    icon: (
      <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
        <path d="M10 2L10 4M10 16L10 18M18 10H16M4 10H2M15.66 15.66L14.24 14.24M5.76 5.76L4.34 4.34M15.66 4.34L14.24 5.76M5.76 14.24L4.34 15.66" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
        <circle cx="10" cy="10" r="3" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    ),
  },
];



export function Sidebar() {
  const pathname = usePathname();
  const [expanded, setExpanded] = useState(false);

  return (
    <>
      {/* Mobile overlay */}
      {expanded && (
        <div
          className="fixed inset-0 bg-black/50 z-30 lg:hidden"
          onClick={() => setExpanded(false)}
        />
      )}

      <aside
        className={`fixed top-0 left-0 h-full z-40 flex flex-col
          border-r transition-all duration-300 ease-out
          ${expanded ? "w-[240px]" : "w-[72px] lg:w-[240px]"}`}
        style={{
          background: "var(--color-bg-secondary)",
          borderColor: "var(--color-border)",
        }}
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
      >
        {/* Logo */}
        <div className="h-16 flex items-center gap-3 px-5 border-b" style={{ borderColor: "var(--color-border)" }}>
          <div
            className="w-8 h-8 rounded-lg flex items-center justify-center shrink-0"
            style={{ background: "var(--color-accent-glow)", border: "1px solid var(--color-accent-dim)" }}
          >
            <span className="mono text-sm font-bold" style={{ color: "var(--color-accent-bright)" }}>
              Py
            </span>
          </div>
          <span
            className={`font-semibold text-lg whitespace-nowrap overflow-hidden transition-opacity duration-200
              ${expanded ? "opacity-100" : "opacity-0 lg:opacity-100"}`}
            style={{ color: "var(--color-text-heading)" }}
          >
            PyCrate
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-4 px-3 flex flex-col gap-1">
          {NAV_ITEMS.map((item) => {
            const isActive = pathname === item.href || (item.href !== "/" && pathname.startsWith(item.href));

            return (
              <Link
                key={item.href}
                href={item.href}
                className={`relative flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium
                  transition-colors duration-150 group`}
                style={{
                  color: isActive ? "var(--color-accent-bright)" : "var(--color-text-secondary)",
                  background: isActive ? "var(--color-accent-glow)" : "transparent",
                }}
              >
                {isActive && (
                  <motion.div
                    layoutId="sidebar-active"
                    className="absolute inset-0 rounded-lg"
                    style={{
                      background: "var(--color-accent-glow)",
                      border: "1px solid rgba(0, 184, 212, 0.2)",
                    }}
                    transition={{ type: "spring", stiffness: 350, damping: 30 }}
                  />
                )}
                <span className="relative z-10 shrink-0">{item.icon}</span>
                <span
                  className={`relative z-10 whitespace-nowrap overflow-hidden transition-opacity duration-200
                    ${expanded ? "opacity-100" : "opacity-0 lg:opacity-100"}`}
                >
                  {item.label}
                </span>
              </Link>
            );
          })}
        </nav>



        {/* Footer */}
        <div className="p-4 border-t" style={{ borderColor: "var(--color-border)" }}>
          <div
            className={`text-xs whitespace-nowrap overflow-hidden transition-opacity duration-200
              ${expanded ? "opacity-100" : "opacity-0 lg:opacity-100"}`}
            style={{ color: "var(--color-text-muted)" }}
          >
            <span className="mono">v0.1.0</span>
          </div>
        </div>
      </aside>
    </>
  );
}
