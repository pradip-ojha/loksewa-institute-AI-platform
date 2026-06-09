import { useState } from "react";
import type { FormEvent } from "react";
import { Settings as SettingsIcon, Mail, KeyRound, ShieldCheck } from "lucide-react";
import api from "../../services/api";
import { useAuth } from "../../context/AuthContext";
import { getErrorMessage } from "../../utils/error";
import { PageHeader, Card, CardHeader, Button, FormField, TextInput, Alert, useToast } from "../../components/ui";

export function AdminSettings() {
  const { user, refreshUser } = useAuth();
  const toast = useToast();

  const initials = (user?.full_name || "A").split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="max-w-2xl space-y-5">
      <PageHeader title="Settings" description="Manage your admin account" icon={<SettingsIcon className="h-5 w-5" />} />

      <Card>
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 text-lg font-bold text-white shadow-glow">
            {initials}
          </div>
          <div className="min-w-0">
            <p className="truncate font-semibold text-gray-900">{user?.full_name}</p>
            <p className="flex items-center gap-1.5 truncate text-sm text-gray-500">
              <Mail className="h-3.5 w-3.5" /> {user?.email}
            </p>
          </div>
          <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-brand-50 px-3 py-1 text-xs font-semibold text-brand-700 ring-1 ring-brand-100">
            <ShieldCheck className="h-3.5 w-3.5" /> Admin
          </span>
        </div>
      </Card>

      <ChangeEmailCard
        currentEmail={user?.email ?? ""}
        onChanged={async () => {
          await refreshUser();
          toast.success("Email updated successfully.");
        }}
      />

      <ChangePasswordCard onChanged={() => toast.success("Password changed successfully.")} />
    </div>
  );
}

function ChangeEmailCard({ currentEmail, onChanged }: { currentEmail: string; onChanged: () => Promise<void> | void }) {
  const [newEmail, setNewEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (newEmail.trim().toLowerCase() === currentEmail.toLowerCase()) {
      setError("The new email is the same as the current one.");
      return;
    }
    setLoading(true);
    try {
      await api.put("/api/admin/profile/email", {
        current_password: password,
        new_email: newEmail.trim(),
      });
      setNewEmail("");
      setPassword("");
      await onChanged();
    } catch (err) {
      setError(getErrorMessage(err, "Failed to change email."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Change Email" subtitle="Your login email address" icon={<Mail className="h-4 w-4" />} />
      {error && <Alert className="mb-4">{error}</Alert>}
      <form onSubmit={submit} className="space-y-4">
        <FormField label="New Email">
          <TextInput type="email" required value={newEmail} onChange={(e) => setNewEmail(e.target.value)} placeholder="you@example.com" autoComplete="email" />
        </FormField>
        <FormField label="Current Password" hint="Required to confirm this change">
          <TextInput type="password" required value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </FormField>
        <Button type="submit" loading={loading}>Update Email</Button>
      </form>
    </Card>
  );
}

function ChangePasswordCard({ onChanged }: { onChanged: () => void }) {
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (newPw !== confirmPw) {
      setError("New passwords do not match.");
      return;
    }
    if (newPw.length < 6) {
      setError("Password must be at least 6 characters.");
      return;
    }
    setLoading(true);
    try {
      await api.put("/api/admin/profile/password", {
        current_password: currentPw,
        new_password: newPw,
      });
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
      onChanged();
    } catch (err) {
      setError(getErrorMessage(err, "Failed to change password."));
    } finally {
      setLoading(false);
    }
  }

  return (
    <Card>
      <CardHeader title="Change Password" icon={<KeyRound className="h-4 w-4" />} />
      {error && <Alert className="mb-4">{error}</Alert>}
      <form onSubmit={submit} className="space-y-4">
        <FormField label="Current Password">
          <TextInput type="password" required value={currentPw} onChange={(e) => setCurrentPw(e.target.value)} autoComplete="current-password" />
        </FormField>
        <FormField label="New Password" hint="At least 6 characters">
          <TextInput type="password" required value={newPw} onChange={(e) => setNewPw(e.target.value)} autoComplete="new-password" />
        </FormField>
        <FormField label="Confirm New Password">
          <TextInput type="password" required value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} autoComplete="new-password" />
        </FormField>
        <Button type="submit" loading={loading}>Change Password</Button>
      </form>
    </Card>
  );
}
