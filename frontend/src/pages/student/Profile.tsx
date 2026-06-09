import React, { useState } from "react";
import { User, Mail, KeyRound } from "lucide-react";
import { useAuth } from "../../context/AuthContext";
import api from "../../services/api";
import { PageHeader, Card, CardHeader, Button, FormField, TextInput, Alert } from "../../components/ui";

export function StudentProfile() {
  const { user } = useAuth();
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");
  const [confirmPw, setConfirmPw] = useState("");
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(false);
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
      await api.put("/api/student/profile/password", {
        current_password: currentPw,
        new_password: newPw,
      });
      setSuccess(true);
      setCurrentPw("");
      setNewPw("");
      setConfirmPw("");
    } catch (err: any) {
      setError(err?.response?.data?.error?.message ?? "Failed to change password.");
    } finally {
      setLoading(false);
    }
  };

  const initials = (user?.full_name || "U").split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase();

  return (
    <div className="space-y-5 pb-20">
      <PageHeader title="Profile" icon={<User className="h-5 w-5" />} />

      <Card>
        <div className="flex items-center gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-brand-500 to-brand-700 text-lg font-bold text-white shadow-glow">
            {initials}
          </div>
          <div className="min-w-0">
            <p className="truncate font-semibold text-gray-900 font-deva">{user?.full_name}</p>
            <p className="flex items-center gap-1.5 truncate text-sm text-gray-500">
              <Mail className="h-3.5 w-3.5" /> {user?.email}
            </p>
          </div>
        </div>
      </Card>

      <Card>
        <CardHeader title="Change Password" icon={<KeyRound className="h-4 w-4" />} />
        {success && <Alert tone="success" className="mb-4">Password changed successfully.</Alert>}
        {error && <Alert className="mb-4">{error}</Alert>}
        <form onSubmit={handleChangePassword} className="space-y-4">
          <FormField label="Current Password">
            <TextInput type="password" required value={currentPw} onChange={(e) => setCurrentPw(e.target.value)} />
          </FormField>
          <FormField label="New Password" hint="At least 6 characters">
            <TextInput type="password" required value={newPw} onChange={(e) => setNewPw(e.target.value)} />
          </FormField>
          <FormField label="Confirm New Password">
            <TextInput type="password" required value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} />
          </FormField>
          <Button type="submit" fullWidth loading={loading}>Change Password</Button>
        </form>
      </Card>
    </div>
  );
}
