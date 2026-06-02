import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { ProtectedRoute } from "./routes/ProtectedRoute";
import { Login } from "./pages/Login";
import { AdminLayout } from "./layouts/AdminLayout";
import { StudentLayout } from "./layouts/StudentLayout";
import { AdminDashboard } from "./pages/admin/Dashboard";
import { AdminStudents } from "./pages/admin/Students";
import { AdminSyllabus } from "./pages/admin/Syllabus";
import { AdminKnowledge } from "./pages/admin/Knowledge";
import { AdminFileUploadTest } from "./pages/admin/FileUploadTest";
import { AdminMCQ } from "./pages/admin/MCQ";
import { StudentDashboard } from "./pages/student/Dashboard";
import { StudentProfile } from "./pages/student/Profile";
import { AdminPlaceholder } from "./pages/admin/Placeholder";
import { StudentPlaceholder } from "./pages/student/Placeholder";

function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          {/* Public */}
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Navigate to="/login" replace />} />

          {/* Admin routes */}
          <Route element={<ProtectedRoute requiredRole="institute_admin" />}>
            <Route path="/admin" element={<AdminLayout />}>
              <Route index element={<Navigate to="dashboard" replace />} />
              <Route path="dashboard" element={<AdminDashboard />} />
              <Route path="syllabus" element={<AdminSyllabus />} />
              <Route path="students" element={<AdminStudents />} />
              <Route path="knowledge" element={<AdminKnowledge />} />
              <Route path="file-test" element={<AdminFileUploadTest />} />
              <Route path="mcq" element={<AdminMCQ />} />
              <Route path="mcq-tests" element={<AdminPlaceholder title="MCQ Tests" description="Create and manage MCQ test sets — available in Stage 6" />} />
              <Route path="video-tutor" element={<AdminPlaceholder title="Video Tutor" description="Upload and process lecture videos — available in Stage 8" />} />
              <Route path="subjective" element={<AdminPlaceholder title="Subjective Tests" description="Create and manage subjective tests — available in Stage 7" />} />
              <Route path="skill-layer" element={<AdminPlaceholder title="Skill Layer" description="Improve AI agent behavior — available in Stage 9" />} />
              <Route path="analytics" element={<AdminPlaceholder title="Analytics" description="View platform analytics — available in Stage 10" />} />
            </Route>
          </Route>

          {/* Student routes */}
          <Route element={<ProtectedRoute requiredRole="student" />}>
            <Route path="/student" element={<StudentLayout />}>
              <Route index element={<Navigate to="dashboard" replace />} />
              <Route path="dashboard" element={<StudentDashboard />} />
              <Route path="mcq-tests" element={<StudentPlaceholder title="MCQ Tests" />} />
              <Route path="video-tutor" element={<StudentPlaceholder title="Video Tutor" />} />
              <Route path="subjective-tests" element={<StudentPlaceholder title="Subjective Tests" />} />
              <Route path="results" element={<StudentPlaceholder title="Results" />} />
              <Route path="profile" element={<StudentProfile />} />
            </Route>
          </Route>

          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}

export default App;
