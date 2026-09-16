"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { AuthLoginForm } from "@/components/auth/AuthLoginForm";
import { normalizeReturnPath } from "@/lib/basePath";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();

  return (
    <AuthShell
      title="登录"
      subtitle="聚梦画布账号独立于主平台，需在本站注册后登录"
    >
      <AuthLoginForm
        appearance="page"
        defaultMode="password"
        onLoggedIn={() => {
          const nextRaw = searchParams.get("next") || "/";
          router.replace(normalizeReturnPath(nextRaw));
        }}
      />
    </AuthShell>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={null}>
      <LoginForm />
    </Suspense>
  );
}
