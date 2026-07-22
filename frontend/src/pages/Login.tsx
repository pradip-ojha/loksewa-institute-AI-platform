import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { motion } from "framer-motion";
import { Mail, Lock, LogIn } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { Button, FormField, TextInput, Alert } from "../components/ui";

export function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await login(email, password);
      const user = JSON.parse(localStorage.getItem("user") ?? "{}");
      navigate(user.role === "institute_admin" ? "/admin/dashboard" : "/student/dashboard", {
        replace: true,
      });
    } catch (err: any) {
      const msg = err?.response?.data?.error?.message ?? "Login failed. Please try again.";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-gradient-to-b from-brand-50/60 to-gray-50 px-4">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.2 }}
        className="w-full max-w-sm"
      >
        <div className="mb-6 text-center">
          <img src="/logo.png" alt="NeuraFix Loksewa" className="mx-auto mb-3 h-14 w-14 object-contain" />
          <h1 className="text-xl font-semibold tracking-tight text-gray-900">
            NeuraFix <span className="text-brand-600">Loksewa</span>
          </h1>
          <p className="mt-1 text-sm text-gray-500 font-deva">लोकसेवा तयारीको स्मार्ट साथी</p>
        </div>

        <form onSubmit={handleSubmit} className="rounded-lg border border-gray-200 bg-white p-8 shadow-card">
          <h2 className="mb-5 text-base font-semibold text-gray-900">Sign in to your account</h2>
          {error && <Alert className="mb-5">{error}</Alert>}

          <div className="space-y-4">
            <FormField label="Email address" htmlFor="email">
              <div className="relative">
                <Mail className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                <TextInput
                  id="email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="pl-9"
                  placeholder="you@example.com"
                  autoComplete="email"
                />
              </div>
            </FormField>

            <FormField label="Password" htmlFor="password">
              <div className="relative">
                <Lock className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-gray-400" />
                <TextInput
                  id="password"
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pl-9"
                  placeholder="••••••••"
                  autoComplete="current-password"
                />
              </div>
            </FormField>
          </div>

          <Button type="submit" fullWidth loading={loading} className="mt-6" icon={<LogIn className="h-4 w-4" />}>
            Sign in
          </Button>
        </form>
      </motion.div>
    </div>
  );
}
