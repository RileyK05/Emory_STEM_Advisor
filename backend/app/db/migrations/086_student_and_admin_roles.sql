-- Only students and administrators remain. Anyone else becomes a student.
UPDATE public.app_users SET role = 'student' WHERE role IN ('advisor', 'auditor');
ALTER TABLE public.app_users DROP CONSTRAINT app_users_role_check;
ALTER TABLE public.app_users ADD CONSTRAINT app_users_role_check
    CHECK (role IN ('student', 'admin'));
