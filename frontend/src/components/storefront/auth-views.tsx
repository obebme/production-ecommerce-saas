"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertCircle,
  CheckCircle2,
  Eye,
  EyeOff,
  LayoutDashboard,
  LogOut,
  Package,
} from "lucide-react";
import { ApiError, extractValidationErrors } from "@/lib/api/client";
import { formatUtcTimestamp, sanitizeReturnTo } from "@/lib/format";
import { useAuth } from "@/providers/auth-provider";
import { StatusBadge } from "@/components/shared/state-feedback";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useToast } from "@/components/ui/toast";

export function LoginView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isLoading: authLoading, sessionExpired, login, clearAuthNotices } = useAuth();

  const rawReturnTo = searchParams.get("returnTo");
  const expiredParam = searchParams.get("expired") === "1";

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [isSubmitting, setIsSubmitting] = React.useState(false);
  const [formError, setFormError] = React.useState<string | null>(null);

  const alertRef = React.useRef<HTMLDivElement>(null);
  const emailInputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (!authLoading && user) {
      const defaultDest = "/account";
      router.replace(sanitizeReturnTo(rawReturnTo, defaultDest));
    }
  }, [authLoading, user, rawReturnTo, router]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    clearAuthNotices();

    if (!email.trim()) {
      setFormError("Please enter your email address.");
      emailInputRef.current?.focus();
      return;
    }
    if (!password) {
      setFormError("Please enter your password.");
      return;
    }

    setIsSubmitting(true);
    try {
      await login(email, password);
      const safeDest = sanitizeReturnTo(rawReturnTo, "/account");
      router.replace(safeDest);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 401 && err.code === "AUTH_INVALID_CREDENTIALS") {
          setFormError("Incorrect email or password.");
        } else if (err.status === 403 && err.code === "AUTH_ACCOUNT_DISABLED") {
          setFormError(
            "Your account has been deactivated. Please contact an administrator."
          );
        } else if (err.status === 429 && err.code === "RATE_LIMIT_EXCEEDED") {
          setFormError("Too many sign-in attempts. Please wait a moment and try again.");
        } else {
          setFormError(err.message);
        }
      } else {
        setFormError("Unable to sign in. Please try again.");
      }
      requestAnimationFrame(() => {
        alertRef.current?.focus();
      });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-md px-4 py-14 sm:px-6">
      <Card>
        <CardHeader className="space-y-1.5">
          <CardTitle className="font-display text-2xl font-medium">Sign in</CardTitle>
          <CardDescription>
            Access your Vellora Store account, saved bag, and order details.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {sessionExpired || expiredParam ? (
            <Alert variant="warning" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertTitle>Session expired</AlertTitle>
              <AlertDescription>
                Your session has expired. Please sign in to continue.
              </AlertDescription>
            </Alert>
          ) : null}

          {formError ? (
            <div ref={alertRef} tabIndex={-1} className="focus:outline-none">
              <Alert variant="destructive" role="alert">
                <AlertCircle className="h-4 w-4" aria-hidden="true" />
                <AlertDescription>{formError}</AlertDescription>
              </Alert>
            </div>
          ) : null}

          <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="login-email">Email</Label>
              <Input
                ref={emailInputRef}
                id="login-email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                disabled={isSubmitting}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="login-password">Password</Label>
              <div className="relative">
                <Input
                  id="login-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  disabled={isSubmitting}
                  className="pr-11"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  className="absolute right-1.5 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <Eye className="h-4 w-4" aria-hidden="true" />
                  )}
                </button>
              </div>
            </div>

            <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
              {isSubmitting ? "Signing in..." : "Sign In"}
            </Button>
          </form>

          <p className="text-center text-sm text-muted-foreground">
            New to Vellora Store?{" "}
            <Link
              href={
                rawReturnTo
                  ? `/register?returnTo=${encodeURIComponent(sanitizeReturnTo(rawReturnTo, "/account"))}`
                  : "/register"
              }
              className="font-medium text-accent underline-offset-4 hover:underline"
            >
              Create an account
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export function RegisterView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { user, isLoading: authLoading, register } = useAuth();

  const rawReturnTo = searchParams.get("returnTo");

  const [fullName, setFullName] = React.useState("");
  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [isSubmitting, setIsSubmitting] = React.useState(false);

  const [formError, setFormError] = React.useState<string | null>(null);
  const [emailExistsConflict, setEmailExistsConflict] = React.useState(false);
  const [fieldErrors, setFieldErrors] = React.useState<{
    full_name?: string;
    email?: string;
    password?: string;
  }>({});

  const fullNameRef = React.useRef<HTMLInputElement>(null);
  const emailRef = React.useRef<HTMLInputElement>(null);
  const passwordRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (!authLoading && user) {
      router.replace(sanitizeReturnTo(rawReturnTo, "/account"));
    }
  }, [authLoading, user, rawReturnTo, router]);

  const passwordMeetsLength = password.length >= 10 && password.length <= 128;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError(null);
    setEmailExistsConflict(false);
    setFieldErrors({});

    const nextFieldErrors: typeof fieldErrors = {};
    const trimmedName = fullName.trim();
    const trimmedEmail = email.trim();

    if (trimmedName.length < 1 || trimmedName.length > 120) {
      nextFieldErrors.full_name = "Full name must be between 1 and 120 characters.";
    }
    if (!trimmedEmail || !trimmedEmail.includes("@")) {
      nextFieldErrors.email = "Enter a valid email address.";
    }
    if (password.length < 10 || password.length > 128) {
      nextFieldErrors.password = "Password must be between 10 and 128 characters.";
    }

    if (Object.keys(nextFieldErrors).length > 0) {
      setFieldErrors(nextFieldErrors);
      if (nextFieldErrors.full_name) {
        fullNameRef.current?.focus();
      } else if (nextFieldErrors.email) {
        emailRef.current?.focus();
      } else if (nextFieldErrors.password) {
        passwordRef.current?.focus();
      }
      return;
    }

    setIsSubmitting(true);
    try {
      await register({ email: trimmedEmail, password, fullName: trimmedName });
      router.replace(sanitizeReturnTo(rawReturnTo, "/account"));
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 409 && err.code === "AUTH_EMAIL_ALREADY_EXISTS") {
          setEmailExistsConflict(true);
          setFieldErrors({ email: "An account with this email already exists." });
          emailRef.current?.focus();
          return;
        }
        if (err.status === 422) {
          const parsed = extractValidationErrors(err);
          const mapped: typeof fieldErrors = {};
          for (const item of parsed) {
            const key = item.loc[item.loc.length - 1];
            if (key === "full_name" || key === "email" || key === "password") {
              mapped[key] = item.msg;
            }
          }
          if (Object.keys(mapped).length > 0) {
            setFieldErrors(mapped);
          } else {
            setFormError(err.message);
          }
          return;
        }
        if (err.status === 429 && err.code === "RATE_LIMIT_EXCEEDED") {
          setFormError("Too many registration attempts. Please try again shortly.");
          return;
        }
        setFormError(err.message);
      } else {
        setFormError("Unable to create your account right now. Please try again.");
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="mx-auto max-w-md px-4 py-14 sm:px-6">
      <Card>
        <CardHeader className="space-y-1.5">
          <CardTitle className="font-display text-2xl font-medium">Create account</CardTitle>
          <CardDescription>
            Save your profile and merge your current shopping bag securely.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {formError ? (
            <Alert variant="destructive" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertDescription>{formError}</AlertDescription>
            </Alert>
          ) : null}

          <form onSubmit={(e) => void handleSubmit(e)} className="space-y-4" noValidate>
            <div className="space-y-1.5">
              <Label htmlFor="register-full-name">Full name</Label>
              <Input
                ref={fullNameRef}
                id="register-full-name"
                type="text"
                autoComplete="name"
                required
                maxLength={120}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                hasError={Boolean(fieldErrors.full_name)}
                aria-describedby={fieldErrors.full_name ? "register-full-name-error" : undefined}
                disabled={isSubmitting}
              />
              {fieldErrors.full_name ? (
                <p id="register-full-name-error" className="text-xs text-destructive">
                  {fieldErrors.full_name}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="register-email">Email</Label>
              <Input
                ref={emailRef}
                id="register-email"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                hasError={Boolean(fieldErrors.email)}
                aria-describedby={fieldErrors.email ? "register-email-error" : undefined}
                disabled={isSubmitting}
              />
              {fieldErrors.email ? (
                <p id="register-email-error" className="text-xs text-destructive">
                  {fieldErrors.email}{" "}
                  {emailExistsConflict ? (
                    <Link href="/login" className="font-medium underline underline-offset-4">
                      Sign in instead
                    </Link>
                  ) : null}
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="register-password">Password</Label>
              <div className="relative">
                <Input
                  ref={passwordRef}
                  id="register-password"
                  type={showPassword ? "text" : "password"}
                  autoComplete="new-password"
                  required
                  minLength={10}
                  maxLength={128}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  hasError={Boolean(fieldErrors.password)}
                  aria-describedby="register-password-hint"
                  disabled={isSubmitting}
                  className="pr-11"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((prev) => !prev)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  className="absolute right-1.5 top-1/2 inline-flex h-8 w-8 -translate-y-1/2 items-center justify-center rounded-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {showPassword ? (
                    <EyeOff className="h-4 w-4" aria-hidden="true" />
                  ) : (
                    <Eye className="h-4 w-4" aria-hidden="true" />
                  )}
                </button>
              </div>
              <p
                id="register-password-hint"
                className={`flex items-center gap-1.5 text-xs ${
                  passwordMeetsLength ? "text-success" : "text-muted-foreground"
                }`}
              >
                {passwordMeetsLength ? (
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
                ) : null}
                <span>10 to 128 characters</span>
              </p>
              {fieldErrors.password ? (
                <p className="text-xs text-destructive">{fieldErrors.password}</p>
              ) : null}
            </div>

            <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
              {isSubmitting ? "Creating account..." : "Create Account"}
            </Button>
          </form>

          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link
              href={
                rawReturnTo
                  ? `/login?returnTo=${encodeURIComponent(sanitizeReturnTo(rawReturnTo, "/account"))}`
                  : "/login"
              }
              className="font-medium text-accent underline-offset-4 hover:underline"
            >
              Sign in
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

export function AccountView() {
  const router = useRouter();
  const { user, isLoading, sessionExpired, updateProfile, logout } = useAuth();
  const { toast } = useToast();

  const [fullName, setFullName] = React.useState("");
  const [isSaving, setIsSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (user) {
      setFullName(user.full_name);
    }
  }, [user]);

  React.useEffect(() => {
    if (!isLoading && !user) {
      const target = sessionExpired
        ? "/login?expired=1&returnTo=%2Faccount"
        : "/login?returnTo=%2Faccount";
      router.replace(target);
    }
  }, [isLoading, user, sessionExpired, router]);

  if (isLoading || !user) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-12 sm:px-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="mt-6 h-64 w-full" />
      </div>
    );
  }

  const trimmed = fullName.trim();
  const isPristine = trimmed === user.full_name;
  const isInvalid = trimmed.length < 1 || trimmed.length > 120;
  const isStaffOrAdmin = user.role === "staff" || user.role === "admin";

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (isPristine || isInvalid) {
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      await updateProfile(trimmed);
      toast({
        title: "Profile updated",
        description: "Your account display name has been saved.",
        variant: "success",
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update profile.");
    } finally {
      setIsSaving(false);
    }
  };

  const handleSignOut = async () => {
    await logout();
    router.push("/");
  };

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-4 py-12 sm:px-6">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-border pb-6">
        <div>
          <h1 className="font-display text-3xl font-medium tracking-[-0.02em] text-foreground">
            My Account
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Manage your profile details and storefront access.
          </p>
        </div>

        <Button variant="outline" onClick={() => void handleSignOut()}>
          <LogOut className="h-4 w-4" aria-hidden="true" />
          Sign Out
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Profile Details</CardTitle>
          <CardDescription>
            Update your name as it appears across the storefront.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          <dl className="grid gap-4 rounded-md border border-border bg-muted/40 p-4 sm:grid-cols-3">
            <div>
              <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Email
              </dt>
              <dd className="mt-1 truncate text-sm font-medium text-foreground">{user.email}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Role
              </dt>
              <dd className="mt-1">
                <StatusBadge domain="role" value={user.role} />
              </dd>
            </div>
            <div>
              <dt className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                Member Since
              </dt>
              <dd className="mt-1 font-mono text-xs text-foreground">
                {formatUtcTimestamp(user.created_at)}
              </dd>
            </div>
          </dl>

          {error ? (
            <Alert variant="destructive" role="alert">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          ) : null}

          <form onSubmit={(e) => void handleSave(e)} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="account-full-name">Full name</Label>
              <Input
                id="account-full-name"
                type="text"
                required
                maxLength={120}
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                hasError={isInvalid}
                disabled={isSaving}
              />
            </div>

            <Button type="submit" disabled={isPristine || isInvalid || isSaving}>
              {isSaving ? "Saving..." : "Save Changes"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Quick Actions</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center gap-3">
          <Button variant="outline" asChild>
            <Link href="/orders">
              <Package className="h-4 w-4" aria-hidden="true" />
              My Orders
            </Link>
          </Button>
          <Button variant="outline" asChild>
            <Link href="/products">Browse Catalog</Link>
          </Button>
          {isStaffOrAdmin ? (
            <Button variant="accent" asChild>
              <Link href="/admin">
                <LayoutDashboard className="h-4 w-4" aria-hidden="true" />
                Open Admin Console
              </Link>
            </Button>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}
