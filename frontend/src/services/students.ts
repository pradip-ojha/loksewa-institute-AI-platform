import api from "./api";
import type { User } from "../types";

export interface StudentListResponse {
  items: User[];
  total: number;
  page: number;
  per_page: number;
}

export interface CreateStudentPayload {
  full_name: string;
  email: string;
  password: string;
  phone?: string;
}

export interface UpdateStudentPayload {
  full_name?: string;
  phone?: string;
}

export const studentsService = {
  async list(page = 1, search = ""): Promise<StudentListResponse> {
    const { data } = await api.get("/api/admin/students", { params: { page, per_page: 20, search } });
    return data;
  },
  async create(payload: CreateStudentPayload): Promise<User> {
    const { data } = await api.post("/api/admin/students", payload);
    return data;
  },
  async update(id: string, payload: UpdateStudentPayload): Promise<User> {
    const { data } = await api.put(`/api/admin/students/${id}`, payload);
    return data;
  },
  async deactivate(id: string): Promise<User> {
    const { data } = await api.post(`/api/admin/students/${id}/deactivate`);
    return data;
  },
  async activate(id: string): Promise<User> {
    const { data } = await api.post(`/api/admin/students/${id}/activate`);
    return data;
  },
  async resetPassword(id: string, newPassword: string): Promise<User> {
    const { data } = await api.post(`/api/admin/students/${id}/reset-password`, { new_password: newPassword });
    return data;
  },
};
