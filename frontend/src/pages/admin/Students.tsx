import React, { useEffect, useState } from "react";
import { studentsService, type CreateStudentPayload } from "../../services/students";
import { examsService, type Exam } from "../../services/exams";
import type { User } from "../../types";

export function AdminStudents() {
  const [students, setStudents] = useState<User[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);

  const [showCreate, setShowCreate] = useState(false);
  const [showEdit, setShowEdit] = useState<User | null>(null);
  const [showReset, setShowReset] = useState<User | null>(null);
  const [showExams, setShowExams] = useState<User | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await studentsService.list(page, search);
      setStudents(res.items);
      setTotal(res.total);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [page, search]);

  const handleDeactivate = async (s: User) => {
    if (!confirm(`Deactivate ${s.full_name}?`)) return;
    await (s.status === "active" ? studentsService.deactivate(s.id) : studentsService.activate(s.id));
    load();
  };

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Students</h2>
          <p className="mt-1 text-sm text-gray-500">{total} total</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600"
        >
          + Add Student
        </button>
      </div>

      <div className="mb-4">
        <input
          type="text"
          placeholder="Search by name or email…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1); }}
          className="w-full max-w-sm rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
        />
      </div>

      <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-gray-100">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-gray-100 bg-gray-50 text-left text-xs font-medium uppercase tracking-wide text-gray-500">
              <th className="px-5 py-3">Name</th>
              <th className="px-5 py-3">Email</th>
              <th className="px-5 py-3">Phone</th>
              <th className="px-5 py-3">Status</th>
              <th className="px-5 py-3">Actions</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {loading ? (
              <tr><td colSpan={5} className="px-5 py-8 text-center text-gray-400">Loading…</td></tr>
            ) : students.length === 0 ? (
              <tr><td colSpan={5} className="px-5 py-8 text-center text-gray-400">No students yet.</td></tr>
            ) : students.map(s => (
              <tr key={s.id} className="hover:bg-gray-50">
                <td className="px-5 py-3 font-medium text-gray-900">{s.full_name}</td>
                <td className="px-5 py-3 text-gray-600">{s.email}</td>
                <td className="px-5 py-3 text-gray-600">{s.phone ?? "—"}</td>
                <td className="px-5 py-3">
                  <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${
                    s.status === "active" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"
                  }`}>
                    {s.status}
                  </span>
                </td>
                <td className="px-5 py-3">
                  <div className="flex gap-2">
                    <button onClick={() => setShowEdit(s)} className="text-xs text-brand-600 hover:underline">Edit</button>
                    <button onClick={() => setShowExams(s)} className="text-xs text-brand-600 hover:underline">Exams</button>
                    <button onClick={() => setShowReset(s)} className="text-xs text-gray-500 hover:underline">Reset PW</button>
                    <button onClick={() => handleDeactivate(s)} className={`text-xs hover:underline ${s.status === "active" ? "text-red-500" : "text-green-600"}`}>
                      {s.status === "active" ? "Deactivate" : "Activate"}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {total > 20 && (
        <div className="mt-4 flex gap-2">
          <button disabled={page === 1} onClick={() => setPage(p => p - 1)} className="rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40">Prev</button>
          <span className="px-3 py-1.5 text-sm text-gray-600">Page {page}</span>
          <button disabled={page * 20 >= total} onClick={() => setPage(p => p + 1)} className="rounded-lg border px-3 py-1.5 text-sm disabled:opacity-40">Next</button>
        </div>
      )}

      {showCreate && <CreateStudentModal onClose={() => setShowCreate(false)} onCreated={load} />}
      {showEdit && <EditStudentModal student={showEdit} onClose={() => setShowEdit(null)} onSaved={load} />}
      {showReset && <ResetPasswordModal student={showReset} onClose={() => setShowReset(null)} />}
      {showExams && <ManageExamsModal student={showExams} onClose={() => setShowExams(null)} />}
    </div>
  );
}

function ManageExamsModal({ student, onClose }: { student: User; onClose: () => void }) {
  const [exams, setExams] = useState<Exam[]>([]);
  const [enrolled, setEnrolled] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([examsService.list(), examsService.listStudentExams(student.id)])
      .then(([all, mine]) => {
        setExams(all.filter((e) => e.status === "active"));
        setEnrolled(new Set(mine.map((m) => m.exam_id)));
      })
      .finally(() => setLoading(false));
  }, [student.id]);

  const toggle = async (examId: string) => {
    setBusy(examId);
    try {
      if (enrolled.has(examId)) {
        await examsService.unenroll(student.id, examId);
        setEnrolled((s) => { const n = new Set(s); n.delete(examId); return n; });
      } else {
        await examsService.enroll(student.id, examId);
        setEnrolled((s) => new Set(s).add(examId));
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal title={`Exams — ${student.full_name}`} onClose={onClose}>
      {loading ? (
        <p className="py-6 text-center text-sm text-gray-400">Loading…</p>
      ) : exams.length === 0 ? (
        <p className="py-6 text-center text-sm text-gray-400">No active exams. Create one first.</p>
      ) : (
        <div className="space-y-2">
          {exams.map((e) => {
            const on = enrolled.has(e.id);
            return (
              <button
                key={e.id}
                onClick={() => toggle(e.id)}
                disabled={busy === e.id}
                className={`flex w-full items-center justify-between rounded-lg border px-3 py-2 text-left text-sm transition-colors disabled:opacity-50 ${
                  on ? "border-brand-200 bg-brand-50" : "border-gray-200 hover:bg-gray-50"
                }`}
              >
                <span>
                  <span className="font-medium text-gray-900">{e.name}</span>
                  <span className="ml-2 text-xs text-gray-400">{e.exam_type}</span>
                </span>
                <span className={`text-xs font-medium ${on ? "text-brand-600" : "text-gray-400"}`}>
                  {on ? "Enrolled ✓" : "Enroll"}
                </span>
              </button>
            );
          })}
        </div>
      )}
      <div className="flex justify-end pt-4">
        <button onClick={onClose} className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600">Done</button>
      </div>
    </Modal>
  );
}

function CreateStudentModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState<CreateStudentPayload>({ full_name: "", email: "", password: "", phone: "" });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await studentsService.create({ ...form, phone: form.phone || undefined });
      onCreated();
      onClose();
    } catch (err: any) {
      setError(err?.response?.data?.error?.message ?? "Failed to create student.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Add Student" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
        <Field label="Full Name"><input required value={form.full_name} onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))} className={inputCls} /></Field>
        <Field label="Email"><input required type="email" value={form.email} onChange={e => setForm(f => ({ ...f, email: e.target.value }))} className={inputCls} /></Field>
        <Field label="Password"><input required type="password" value={form.password} onChange={e => setForm(f => ({ ...f, password: e.target.value }))} className={inputCls} placeholder="Min 6 characters" /></Field>
        <Field label="Phone (optional)"><input value={form.phone} onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} className={inputCls} /></Field>
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-600 hover:bg-gray-100">Cancel</button>
          <button type="submit" disabled={loading} className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60">
            {loading ? "Creating…" : "Create Student"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditStudentModal({ student, onClose, onSaved }: { student: User; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState({ full_name: student.full_name, phone: student.phone ?? "" });
  const [loading, setLoading] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await studentsService.update(student.id, { full_name: form.full_name, phone: form.phone || undefined });
      onSaved();
      onClose();
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title="Edit Student" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <Field label="Full Name"><input required value={form.full_name} onChange={e => setForm(f => ({ ...f, full_name: e.target.value }))} className={inputCls} /></Field>
        <Field label="Phone (optional)"><input value={form.phone} onChange={e => setForm(f => ({ ...f, phone: e.target.value }))} className={inputCls} /></Field>
        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-600 hover:bg-gray-100">Cancel</button>
          <button type="submit" disabled={loading} className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60">
            {loading ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ResetPasswordModal({ student, onClose }: { student: User; onClose: () => void }) {
  const [pw, setPw] = useState("");
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await studentsService.resetPassword(student.id, pw);
      setDone(true);
    } catch (err: any) {
      setError(err?.response?.data?.error?.message ?? "Failed.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Modal title={`Reset Password — ${student.full_name}`} onClose={onClose}>
      {done ? (
        <div className="text-center">
          <p className="mb-4 text-sm text-green-700">Password reset successfully.</p>
          <button onClick={onClose} className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white">Close</button>
        </div>
      ) : (
        <form onSubmit={submit} className="space-y-4">
          {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>}
          <Field label="New Password">
            <input required type="password" value={pw} onChange={e => setPw(e.target.value)} className={inputCls} placeholder="Min 6 characters" />
          </Field>
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-600 hover:bg-gray-100">Cancel</button>
            <button type="submit" disabled={loading} className="rounded-lg bg-brand-500 px-4 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-60">
              {loading ? "Resetting…" : "Reset Password"}
            </button>
          </div>
        </form>
      )}
    </Modal>
  );
}

const inputCls = "w-full rounded-lg border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1.5 block text-sm font-medium text-gray-700">{label}</label>
      {children}
    </div>
  );
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
        <div className="mb-5 flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600">✕</button>
        </div>
        {children}
      </div>
    </div>
  );
}
