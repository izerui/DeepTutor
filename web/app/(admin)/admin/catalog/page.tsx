"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import CatalogAdmin from "@/components/catalog/CatalogAdmin";
import { fetchAuthStatus } from "@/lib/auth";

/** Classification and tags (platform batch 2 §8): administrators only. */
export default function AdminCatalogPage() {
  const router = useRouter();
  const [allowed, setAllowed] = useState(false);
  useEffect(() => {
    fetchAuthStatus().then((status) => {
      if (!status?.authenticated) {
        router.replace("/login");
        return;
      }
      if (status.role !== "admin") {
        router.replace("/");
        return;
      }
      setAllowed(true);
    });
  }, [router]);
  if (!allowed) return <div aria-busy="true" className="m-8 h-40 animate-pulse rounded-xl bg-[var(--muted)]" />;
  return <CatalogAdmin />;
}
