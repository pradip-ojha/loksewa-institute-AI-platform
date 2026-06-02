import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import type { UserRole } from "../types";

interface Props {
  requiredRole: UserRole;
  redirectTo?: string;
}

export function ProtectedRoute({ requiredRole, redirectTo = "/login" }: Props) {
  const { user, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="h-8 w-8 animate-spin rounded-full border-4 border-brand-500 border-t-transparent" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to={redirectTo} replace />;
  }

  if (user.role !== requiredRole) {
    const fallback = user.role === "institute_admin" ? "/admin/dashboard" : "/student/dashboard";
    return <Navigate to={fallback} replace />;
  }

  return <Outlet />;
}
