import React, { useEffect, useState } from "react";
import { Users, Plus, Pencil, GraduationCap, KeyRound, UserX, UserCheck } from "lucide-react";
import { studentsService, type CreateStudentPayload } from "../../services/students";
import { examsService, type Exam } from "../../services/exams";
import type { User } from "../../types";
import {
  PageHeader,
  Button,
  Modal,
  FormField,
  TextInput,
  Alert,
  StatusBadge,
  DataTable,
  SearchInput,
  Pagination,
  Menu,
  ConfirmDialog,
  type Column,
} from "../../components/ui";

const PAGE_SIZE = 20;

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
  const [confirmToggle, setConfirmToggle] = useState<User | null>(null);
  const [toggleBusy, setToggleBusy] = useState(false);

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

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, search]);

  const doToggle = async () => {
    if (!confirmToggle) return;
    setToggleBusy(true);
    try {
      await (confirmToggle.status === "active"
        ? studentsService.deactivate(confirmToggle.id)
        : studentsService.activate(confirmToggle.id));
      setConfirmToggle(null);
      load();
    } finally {
      setToggleBusy(false);
    }
  };

  const columns: Column<User>[] = [
    {
      key: "name",
      header: "Name",
      accessor: (s) => s.full_name,
      render: (s) => <span className="font-medium text-gray-900">{s.full_name}</span>,
    },
    { key: "email", header: "Email", accessor: (s) => s.email, render: (s) => <span className="text-gray-600">{s.email}</span> },
    { key: "phone", header: "Phone", render: (s) => <span className="text-gray-600">{s.phone ?? "—"}</span> },
    { key: "status", header: "Status", render: (s) => <StatusBadge status={s.status} /> },
    {
      key: "actions",
      header: "",
      align: "right",
      width: "3rem",
      render: (s) => (
        <Menu
          items={[
            { label: "Edit", icon: <Pencil className="h-4 w-4" />, onClick: () => setShowEdit(s) },
            { label: "Manage exams", icon: <GraduationCap className="h-4 w-4" />, onClick: () => setShowExams(s) },
            { label: "Reset password", icon: <KeyRound className="h-4 w-4" />, onClick: () => setShowReset(s) },
            s.status === "active"
              ? { label: "Deactivate", icon: <UserX className="h-4 w-4" />, tone: "danger" as const, onClick: () => setConfirmToggle(s) }
              : { label: "Activate", icon: <UserCheck className="h-4 w-4" />, onClick: () => setConfirmToggle(s) },
          ]}
        />
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="Students"
        description={`${total} total`}
        icon={<Users className="h-5 w-5" />}
        actions={
          <Button icon={<Plus className="h-4 w-4" />} onClick={() => setShowCreate(true)}>
            Add Student
          </Button>
        }
      />

      <DataTable
        columns={columns}
        rows={students}
        rowKey={(s) => s.id}
        loading={loading}
        toolbar={
          <SearchInput
            value={search}
            onChange={(v) => {
              setSearch(v);
              setPage(1);
            }}
            placeholder="Search by name or email…"
          />
        }
        footer={total > PAGE_SIZE ? <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} /> : undefined}
      />

      {showCreate && <CreateStudentModal onClose={() => setShowCreate(false)} onCreated={load} />}
      {showEdit && <EditStudentModal student={showEdit} onClose={() => setShowEdit(null)} onSaved={load} />}
      {showReset && <ResetPasswordModal student={showReset} onClose={() => setShowReset(null)} />}
      {showExams && <ManageExamsModal student={showExams} onClose={() => setShowExams(null)} />}

      <ConfirmDialog
        open={!!confirmToggle}
        title={confirmToggle?.status === "active" ? "Deactivate student" : "Activate student"}
        description={
          confirmToggle
            ? `${confirmToggle.status === "active" ? "Deactivate" : "Activate"} ${confirmToggle.full_name}?`
            : ""
        }
        confirmLabel={confirmToggle?.status === "active" ? "Deactivate" : "Activate"}
        tone={confirmToggle?.status === "active" ? "danger" : "default"}
        loading={toggleBusy}
        onConfirm={doToggle}
        onClose={() => setConfirmToggle(null)}
      />
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
        setEnrolled((s) => {
          const n = new Set(s);
          n.delete(examId);
          return n;
        });
      } else {
        await examsService.enroll(student.id, examId);
        setEnrolled((s) => new Set(s).add(examId));
      }
    } finally {
      setBusy(null);
    }
  };

  return (
    <Modal
      open
      onClose={onClose}
      title={`Exams — ${student.full_name}`}
      footer={<Button onClick={onClose}>Done</Button>}
    >
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
                className={`flex w-full items-center justify-between rounded-md border px-3 py-2 text-left text-sm transition-colors disabled:opacity-50 ${
                  on ? "border-brand-200 bg-brand-50" : "border-gray-200 hover:bg-gray-50"
                }`}
              >
                <span>
                  <span className="font-medium text-gray-900">{e.name}</span>
                  <span className="ml-2 text-xs text-gray-400">{e.exam_type}</span>
                </span>
                <span className={`text-xs font-medium ${on ? "text-brand-600" : "text-gray-400"}`}>
                  {on ? "Enrolled" : "Enroll"}
                </span>
              </button>
            );
          })}
        </div>
      )}
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
    <Modal
      open
      onClose={onClose}
      title="Add Student"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="create-student-form" loading={loading}>
            Create Student
          </Button>
        </>
      }
    >
      <form id="create-student-form" onSubmit={submit} className="space-y-4">
        {error && <Alert>{error}</Alert>}
        <FormField label="Full Name" required>
          <TextInput required value={form.full_name} onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))} />
        </FormField>
        <FormField label="Email" required>
          <TextInput required type="email" value={form.email} onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))} />
        </FormField>
        <FormField label="Password" required>
          <TextInput required type="password" value={form.password} onChange={(e) => setForm((f) => ({ ...f, password: e.target.value }))} placeholder="Min 6 characters" />
        </FormField>
        <FormField label="Phone (optional)">
          <TextInput value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
        </FormField>
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
    <Modal
      open
      onClose={onClose}
      title="Edit Student"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" form="edit-student-form" loading={loading}>
            Save Changes
          </Button>
        </>
      }
    >
      <form id="edit-student-form" onSubmit={submit} className="space-y-4">
        <FormField label="Full Name" required>
          <TextInput required value={form.full_name} onChange={(e) => setForm((f) => ({ ...f, full_name: e.target.value }))} />
        </FormField>
        <FormField label="Phone (optional)">
          <TextInput value={form.phone} onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))} />
        </FormField>
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
    <Modal
      open
      onClose={onClose}
      title={`Reset Password — ${student.full_name}`}
      footer={
        done ? (
          <Button onClick={onClose}>Close</Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" form="reset-pw-form" loading={loading}>
              Reset Password
            </Button>
          </>
        )
      }
    >
      {done ? (
        <Alert tone="success">Password reset successfully.</Alert>
      ) : (
        <form id="reset-pw-form" onSubmit={submit} className="space-y-4">
          {error && <Alert>{error}</Alert>}
          <FormField label="New Password" required>
            <TextInput required type="password" value={pw} onChange={(e) => setPw(e.target.value)} placeholder="Min 6 characters" />
          </FormField>
        </form>
      )}
    </Modal>
  );
}
