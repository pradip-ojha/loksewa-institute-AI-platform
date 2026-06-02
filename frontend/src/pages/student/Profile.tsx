import React, { useState } from "react";
import { useAuth } from "../../context/AuthContext";
import api from "../../services/api";

export function StudentProfile() {
  const { user, logout } = useAuth();
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

  return (
    <div className="space-y-5">
      <h2 className="text-lg font-semibold text-gray-900">Profile</h2>

      <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        <h3 className="mb-4 text-sm font-semibold text-gray-700">Account Info</h3>
        <div className="space-y-3">
          <div>
            <p className="text-xs text-gray-400">Full Name</p>
            <p className="text-sm font-medium text-gray-900">{user?.full_name}</p>
          </div>
          <div>
            <p className="text-xs text-gray-400">Email</p>
            <p className="text-sm text-gray-700">{user?.email}</p>
          </div>
        </div>
      </div>

      <div className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-gray-100">
        <h3 className="mb-4 text-sm font-semibold text-gray-700">Change Password</h3>
        {success && (
          <div className="mb-4 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-700">
            Password changed successfully.
          </div>
        )}
        {error && (
          <div className="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>
        )}
        <form onSubmit={handleChangePassword} className="space-y-4">
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Current Password</label>
            <input
              type="password" required value={currentPw}
              onChange={e => setCurrentPw(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">New Password</label>
            <input
              type="password" required value={newPw}
              onChange={e => setNewPw(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
            />
          </div>
          <div>
            <label className="mb-1.5 block text-sm font-medium text-gray-700">Confirm New Password</label>
            <input
              type="password" required value={confirmPw}
              onChange={e => setConfirmPw(e.target.value)}
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
            />
          </div>
          <button
            type="submit" disabled={loading}
            className="w-full rounded-lg bg-brand-500 py-2.5 text-sm font-semibold text-white hover:bg-brand-600 disabled:opacity-60"
          >
            {loading ? "Changing…" : "Change Password"}
          </button>
        </form>
      </div>
    </div>
  );
}
