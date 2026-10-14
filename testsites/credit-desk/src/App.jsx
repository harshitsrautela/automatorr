import React, { useState, useEffect, useMemo } from 'react';
import { createClient } from '@supabase/supabase-js';
import {
  Database,
  Users,
  Building,
  Globe,
  Plus,
  Search,
  RefreshCw,
  FileText,
  Download,
  CheckCircle,
  AlertCircle,
  Trash2,
  Edit3,
  ChevronLeft,
  ChevronRight,
  X,
  Copy,
  Code,
  Settings,
  Filter,
  Sparkles,
  ShieldCheck,
  HelpCircle,
  Check,
  Info
} from 'lucide-react';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || '';
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || '';

const supabase =
  supabaseUrl && supabaseAnonKey
    ? createClient(supabaseUrl, supabaseAnonKey)
    : null;

const SEED_PEOPLE = [
  {
    id: "a1b2c3d4-e5f6-4a5b-8c9d-0123456789ab",
    first_name: "Sarah",
    last_name: "Connor",
    email: "sarah.connor@cyberdyne.io",
    phone: "+1-555-0192",
    date_of_birth: "1985-05-12",
    gender: "Female",
    company: "Cyberdyne Systems",
    job_title: "Security Specialist",
    country: "United States",
    city: "Los Angeles",
    address: "123 Future Way",
    postal_code: "90001",
    department: "Security",
    employment_type: "Full Time",
    experience_years: 8,
    salary: 95000,
    newsletter: true,
    notes: "Seed data",
    created_at: "2026-09-01T09:00:00Z"
  },
  {
    id: "b2c3d4e5-f6a7-4b6c-9d0e-123456789abc",
    first_name: "Michael",
    last_name: "Chen",
    email: "michael.chen@acme.com",
    phone: "+1-555-0193",
    date_of_birth: "1990-08-21",
    gender: "Male",
    company: "Acme Corporation",
    job_title: "Software Engineer",
    country: "United States",
    city: "San Francisco",
    address: "45 Market Street",
    postal_code: "94105",
    department: "Engineering",
    employment_type: "Full Time",
    experience_years: 6,
    salary: 120000,
    newsletter: true,
    notes: "Seed data",
    created_at: "2026-09-02T10:30:00Z"
  },
  {
    id: "c3d4e5f6-a7b8-4c9d-0e1f-23456789abcd",
    first_name: "Priya",
    last_name: "Sharma",
    email: "priya.sharma@techcorp.in",
    phone: "+91-9876543210",
    date_of_birth: "1994-02-14",
    gender: "Female",
    company: "TechCorp",
    job_title: "Product Manager",
    country: "India",
    city: "Bangalore",
    address: "78 MG Road",
    postal_code: "560001",
    department: "Product",
    employment_type: "Full Time",
    experience_years: 5,
    salary: 1800000,
    newsletter: false,
    notes: "Seed data",
    created_at: "2026-09-03T08:15:00Z"
  },
  {
    id: "d4e5f6a7-b8c9-4d0e-1f2a-3456789abcde",
    first_name: "James",
    last_name: "Wilson",
    email: "james.wilson@globaltech.co.uk",
    phone: "+44-20-7946-0958",
    date_of_birth: "1988-11-03",
    gender: "Male",
    company: "GlobalTech",
    job_title: "Engineering Manager",
    country: "United Kingdom",
    city: "London",
    address: "12 King Street",
    postal_code: "EC1A 1BB",
    department: "Engineering",
    employment_type: "Full Time",
    experience_years: 12,
    salary: 110000,
    newsletter: true,
    notes: "Seed data",
    created_at: "2026-09-04T11:45:00Z"
  },
  {
    id: "e5f6a7b8-c9d0-4e1f-2a3b-456789abcdef",
    first_name: "Emma",
    last_name: "Dubois",
    email: "emma.dubois@paristech.fr",
    phone: "+33-1-42-68-53-00",
    date_of_birth: "1992-06-30",
    gender: "Female",
    company: "Paris Tech",
    job_title: "Data Scientist",
    country: "France",
    city: "Paris",
    address: "25 Rue de Rivoli",
    postal_code: "75001",
    department: "Data",
    employment_type: "Full Time",
    experience_years: 7,
    salary: 72000,
    newsletter: true,
    notes: "Seed data",
    created_at: "2026-09-05T14:20:00Z"
  },
  {
    id: "f6a7b8c9-d0e1-4f2a-3b4c-56789abcdef0",
    first_name: "David",
    last_name: "Kim",
    email: "david.kim@seoulsoft.kr",
    phone: "+82-2-1234-5678",
    date_of_birth: "1987-09-17",
    gender: "Male",
    company: "SeoulSoft",
    job_title: "DevOps Engineer",
    country: "South Korea",
    city: "Seoul",
    address: "100 Gangnam-daero",
    postal_code: "06236",
    department: "Infrastructure",
    employment_type: "Full Time",
    experience_years: 10,
    salary: 85000000,
    newsletter: false,
    notes: "Seed data",
    created_at: "2026-09-06T07:50:00Z"
  },
  {
    id: "a7b8c9d0-e1f2-4a3b-4c5d-6789abcdef01",
    first_name: "Olivia",
    last_name: "Taylor",
    email: "olivia.taylor@australianetwork.au",
    phone: "+61-2-9374-4000",
    date_of_birth: "1991-04-22",
    gender: "Female",
    company: "Australia Network",
    job_title: "Marketing Manager",
    country: "Australia",
    city: "Sydney",
    address: "88 George Street",
    postal_code: "2000",
    department: "Marketing",
    employment_type: "Full Time",
    experience_years: 8,
    salary: 98000,
    newsletter: true,
    notes: "Seed data",
    created_at: "2026-09-07T12:10:00Z"
  },
  {
    id: "b8c9d0e1-f2a3-4b4c-5d6e-789abcdef012",
    first_name: "Carlos",
    last_name: "Garcia",
    email: "carlos.garcia@iberiatech.es",
    phone: "+34-91-123-4567",
    date_of_birth: "1989-12-08",
    gender: "Male",
    company: "Iberia Tech",
    job_title: "UX Designer",
    country: "Spain",
    city: "Madrid",
    address: "42 Gran Via",
    postal_code: "28013",
    department: "Design",
    employment_type: "Contract",
    experience_years: 9,
    salary: 65000,
    newsletter: true,
    notes: "Seed data",
    created_at: "2026-09-08T09:40:00Z"
  },
  {
    id: "c9d0e1f2-a3b4-4c5d-6e7f-89abcdef0123",
    first_name: "Sophia",
    last_name: "Muller",
    email: "sophia.muller@berlinlabs.de",
    phone: "+49-30-123456",
    date_of_birth: "1993-01-25",
    gender: "Female",
    company: "Berlin Labs",
    job_title: "Research Scientist",
    country: "Germany",
    city: "Berlin",
    address: "15 Unter den Linden",
    postal_code: "10117",
    department: "Research",
    employment_type: "Full Time",
    experience_years: 6,
    salary: 78000,
    newsletter: false,
    notes: "Seed data",
    created_at: "2026-09-09T15:05:00Z"
  },
  {
    id: "d0e1f2a3-b4c5-4d6e-7f8a-9abcdef01234",
    first_name: "Liam",
    last_name: "O'Connor",
    email: "liam.oconnor@dublintech.ie",
    phone: "+353-1-555-0123",
    date_of_birth: "1986-07-11",
    gender: "Male",
    company: "Dublin Tech",
    job_title: "Solutions Architect",
    country: "Ireland",
    city: "Dublin",
    address: "7 O'Connell Street",
    postal_code: "D01",
    department: "Architecture",
    employment_type: "Full Time",
    experience_years: 14,
    salary: 105000,
    newsletter: true,
    notes: "Seed data",
    created_at: "2026-09-10T10:25:00Z"
  }
];

const EMPTY_FORM = {
  first_name: "",
  last_name: "",
  email: "",
  phone: "",
  date_of_birth: "",
  gender: "",
  company: "",
  job_title: "",
  country: "",
  city: "",
  address: "",
  postal_code: "",
  department: "",
  employment_type: "",
  experience_years: "",
  salary: "",
  newsletter: false,
  notes: ""
};

const COUNTRIES = [
  "Afghanistan", "Albania", "Algeria", "Argentina", "Armenia", "Australia",
  "Austria", "Azerbaijan", "Bahrain", "Bangladesh", "Belarus", "Belgium",
  "Bolivia", "Bosnia and Herzegovina", "Brazil", "Bulgaria", "Cambodia",
  "Cameroon", "Canada", "Chile", "China", "Colombia", "Costa Rica", "Croatia",
  "Cuba", "Cyprus", "Czech Republic", "Denmark", "Dominican Republic",
  "Ecuador", "Egypt", "El Salvador", "Estonia", "Ethiopia", "Finland",
  "France", "Georgia", "Germany", "Ghana", "Greece", "Guatemala", "Honduras",
  "Hong Kong", "Hungary", "Iceland", "India", "Indonesia", "Iran", "Iraq",
  "Ireland", "Israel", "Italy", "Ivory Coast", "Jamaica", "Japan", "Jordan",
  "Kazakhstan", "Kenya", "Kuwait", "Latvia", "Lebanon", "Lithuania",
  "Luxembourg", "Malaysia", "Malta", "Mexico", "Moldova", "Monaco",
  "Mongolia", "Montenegro", "Morocco", "Myanmar", "Nepal", "Netherlands",
  "New Zealand", "Nigeria", "North Macedonia", "Norway", "Oman", "Pakistan",
  "Panama", "Paraguay", "Peru", "Philippines", "Poland", "Portugal", "Qatar",
  "Romania", "Russia", "Rwanda", "Saudi Arabia", "Senegal", "Serbia",
  "Singapore", "Slovakia", "Slovenia", "South Africa", "South Korea",
  "Spain", "Sri Lanka", "Sweden", "Switzerland", "Taiwan", "Tanzania",
  "Thailand", "Tunisia", "Turkey", "Uganda", "Ukraine", "United Arab Emirates",
  "United Kingdom", "United States", "Uruguay", "Uzbekistan", "Venezuela",
  "Vietnam", "Zambia", "Zimbabwe"
];

const DEPARTMENTS = [
  "Engineering",
  "Product",
  "Design",
  "Marketing",
  "Sales",
  "Finance",
  "Human Resources",
  "Data",
  "Research",
  "Security",
  "Infrastructure",
  "Architecture",
  "Operations"
];

const EMPLOYMENT_TYPES = [
  "Full Time",
  "Part Time",
  "Contract",
  "Intern",
  "Freelance"
];

const GENDERS = [
  "Male",
  "Female",
  "Non-binary",
  "Prefer not to say"
];

function formatDate(value) {
  if (!value) return "—";

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return date.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric"
  });
}

function formatCurrency(value) {
  if (value === null || value === undefined || value === "") {
    return "—";
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0
  }).format(Number(value));
}

function normalizePerson(person) {
  return {
    ...person,
    experience_years:
      person.experience_years === null ||
      person.experience_years === undefined ||
      person.experience_years === ""
        ? null
        : Number(person.experience_years),
    salary:
      person.salary === null ||
      person.salary === undefined ||
      person.salary === ""
        ? null
        : Number(person.salary),
    newsletter: Boolean(person.newsletter)
  };
}

function validateForm(form) {
  const errors = {};

  if (!form.first_name.trim()) {
    errors.first_name = "First name is required.";
  }

  if (!form.last_name.trim()) {
    errors.last_name = "Last name is required.";
  }

  if (!form.email.trim()) {
    errors.email = "Email is required.";
  } else if (
    !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())
  ) {
    errors.email = "Enter a valid email address.";
  }

  if (form.experience_years !== "") {
    const experience = Number(form.experience_years);

    if (!Number.isFinite(experience) || experience < 0) {
      errors.experience_years =
        "Experience must be a non-negative number.";
    }
  }

  if (form.salary !== "") {
    const salary = Number(form.salary);

    if (!Number.isFinite(salary) || salary < 0) {
      errors.salary = "Salary must be a non-negative number.";
    }
  }

  return errors;
}

function StatCard({
  icon: Icon,
  label,
  value,
  description
}) {
  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-slate-500">
            {label}
          </p>

          <p className="mt-2 text-3xl font-semibold tracking-tight text-slate-900">
            {value}
          </p>

          {description && (
            <p className="mt-1 text-xs text-slate-400">
              {description}
            </p>
          )}
        </div>

        <div className="rounded-xl bg-slate-100 p-3 text-slate-700">
          <Icon size={21} />
        </div>
      </div>
    </div>
  );
}

function Field({
  label,
  name,
  value,
  onChange,
  error,
  required = false,
  type = "text",
  placeholder = "",
  disabled = false,
  children
}) {
  const id = `field-${name}`;

  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 block text-sm font-medium text-slate-700"
      >
        {label}
        {required && (
          <span className="ml-1 text-red-500" aria-hidden="true">
            *
          </span>
        )}
      </label>

      {children || (
        <input
          id={id}
          name={name}
          type={type}
          value={value ?? ""}
          onChange={onChange}
          placeholder={placeholder}
          disabled={disabled}
          required={required}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? `${id}-error` : undefined}
          className={[
            "w-full rounded-xl border bg-white px-3.5 py-2.5 text-sm",
            "text-slate-900 outline-none transition",
            "placeholder:text-slate-400",
            "focus:border-slate-500 focus:ring-2 focus:ring-slate-200",
            "disabled:cursor-not-allowed disabled:bg-slate-100",
            error
              ? "border-red-400 focus:border-red-500 focus:ring-red-100"
              : "border-slate-300"
          ].join(" ")}
        />
      )}

      {error && (
        <p
          id={`${id}-error`}
          className="mt-1.5 text-xs text-red-600"
        >
          {error}
        </p>
      )}
    </div>
  );
}

function SelectField({
  label,
  name,
  value,
  onChange,
  options,
  error,
  required = false,
  placeholder = "Select..."
}) {
  const id = `field-${name}`;

  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 block text-sm font-medium text-slate-700"
      >
        {label}
        {required && (
          <span className="ml-1 text-red-500" aria-hidden="true">
            *
          </span>
        )}
      </label>

      <select
        id={id}
        name={name}
        value={value ?? ""}
        onChange={onChange}
        required={required}
        aria-invalid={Boolean(error)}
        className={[
          "w-full rounded-xl border bg-white px-3.5 py-2.5",
          "text-sm text-slate-900 outline-none transition",
          "focus:border-slate-500 focus:ring-2 focus:ring-slate-200",
          error
            ? "border-red-400"
            : "border-slate-300"
        ].join(" ")}
      >
        <option value="">{placeholder}</option>

        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>

      {error && (
        <p className="mt-1.5 text-xs text-red-600">
          {error}
        </p>
      )}
    </div>
  );
}

function EmptyState({ onAdd }) {
  return (
    <div className="flex min-h-[350px] flex-col items-center justify-center px-6 text-center">
      <div className="mb-4 rounded-2xl bg-slate-100 p-4 text-slate-500">
        <Users size={28} />
      </div>

      <h3 className="text-base font-semibold text-slate-900">
        No people found
      </h3>

      <p className="mt-1 max-w-sm text-sm text-slate-500">
        Add your first person to start building your directory.
      </p>

      <button
        type="button"
        onClick={onAdd}
        className="mt-5 inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800"
      >
        <Plus size={17} />
        Add Person
      </button>
    </div>
  );
}

function App() {
  const [people, setPeople] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [view, setView] = useState("people");

  const [showForm, setShowForm] = useState(false);
  const [editingPerson, setEditingPerson] = useState(null);

  const [form, setForm] = useState(EMPTY_FORM);
  const [errors, setErrors] = useState({});

  const [search, setSearch] = useState("");
  const [countryFilter, setCountryFilter] = useState("");
  const [departmentFilter, setDepartmentFilter] = useState("");
  const [employmentFilter, setEmploymentFilter] = useState("");

  const [page, setPage] = useState(1);
  const pageSize = 8;

  const [notification, setNotification] = useState(null);

  const [selectedPerson, setSelectedPerson] = useState(null);

  const [deleteTarget, setDeleteTarget] = useState(null);

  const isConfigured = Boolean(supabase);

  const showNotification = (type, message) => {
    setNotification({
      type,
      message
    });

    window.setTimeout(() => {
      setNotification(null);
    }, 4000);
  };

  const loadPeople = async () => {
    setLoading(true);

    try {
      if (!supabase) {
        const localData =
          JSON.parse(
            localStorage.getItem("browser-data-entry-people") ||
              "null"
          ) || SEED_PEOPLE;

        if (!localStorage.getItem("browser-data-entry-people")) {
          localStorage.setItem(
            "browser-data-entry-people",
            JSON.stringify(localData)
          );
        }

        setPeople(localData.map(normalizePerson));
        return;
      }

      const { data, error } = await supabase
        .from("people")
        .select("*")
        .order("created_at", { ascending: false });

      if (error) {
        throw error;
      }

      setPeople((data || []).map(normalizePerson));
    } catch (error) {
      console.error(error);

      showNotification(
        "error",
        error?.message ||
          "Unable to load people."
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadPeople();
  }, []);

  const filteredPeople = useMemo(() => {
    const query = search.trim().toLowerCase();

    return people.filter((person) => {
      const matchesSearch =
        !query ||
        [
          person.first_name,
          person.last_name,
          person.email,
          person.company
        ]
          .filter(Boolean)
          .some((value) =>
            String(value).toLowerCase().includes(query)
          );

      const matchesCountry =
        !countryFilter ||
        person.country === countryFilter;

      const matchesDepartment =
        !departmentFilter ||
        person.department === departmentFilter;

      const matchesEmployment =
        !employmentFilter ||
        person.employment_type === employmentFilter;

      return (
        matchesSearch &&
        matchesCountry &&
        matchesDepartment &&
        matchesEmployment
      );
    });
  }, [
    people,
    search,
    countryFilter,
    departmentFilter,
    employmentFilter
  ]);

  const totalPages = Math.max(
    1,
    Math.ceil(filteredPeople.length / pageSize)
  );

  const currentPage = Math.min(page, totalPages);

  const paginatedPeople = useMemo(() => {
    const start = (currentPage - 1) * pageSize;

    return filteredPeople.slice(
      start,
      start + pageSize
    );
  }, [filteredPeople, currentPage]);

  useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages);
    }
  }, [page, totalPages]);

  const stats = useMemo(() => {
    const today = new Date().toISOString().slice(0, 10);

    const companies = new Set(
      people
        .map((person) => person.company)
        .filter(Boolean)
    );

    const countries = new Set(
      people
        .map((person) => person.country)
        .filter(Boolean)
    );

    const addedToday = people.filter((person) =>
      String(person.created_at || "").startsWith(today)
    ).length;

    return {
      total: people.length,
      addedToday,
      companies: companies.size,
      countries: countries.size
    };
  }, [people]);

  const openCreateForm = () => {
    setEditingPerson(null);
    setForm(EMPTY_FORM);
    setErrors({});
    setShowForm(true);
  };

  const openEditForm = (person) => {
    setEditingPerson(person);

    setForm({
      first_name: person.first_name || "",
      last_name: person.last_name || "",
      email: person.email || "",
      phone: person.phone || "",
      date_of_birth: person.date_of_birth || "",
      gender: person.gender || "",
      company: person.company || "",
      job_title: person.job_title || "",
      country: person.country || "",
      city: person.city || "",
      address: person.address || "",
      postal_code: person.postal_code || "",
      department: person.department || "",
      employment_type: person.employment_type || "",
      experience_years:
        person.experience_years ?? "",
      salary:
        person.salary ?? "",
      newsletter: Boolean(person.newsletter),
      notes: person.notes || ""
    });

    setErrors({});
    setShowForm(true);
  };

  const closeForm = () => {
    if (saving) return;

    setShowForm(false);
    setEditingPerson(null);
    setErrors({});
  };

  const handleChange = (event) => {
    const {
      name,
      value,
      type,
      checked
    } = event.target;

    setForm((current) => ({
      ...current,
      [name]:
        type === "checkbox"
          ? checked
          : value
    }));

    setErrors((current) => {
      if (!current[name]) {
        return current;
      }

      const next = {
        ...current
      };

      delete next[name];

      return next;
    });
  };

  const persistLocal = (nextPeople) => {
    localStorage.setItem(
      "browser-data-entry-people",
      JSON.stringify(nextPeople)
    );

    setPeople(nextPeople.map(normalizePerson));
  };

  const submitForm = async (event) => {
    event.preventDefault();

    const validationErrors =
      validateForm(form);

    if (Object.keys(validationErrors).length) {
      setErrors(validationErrors);

      showNotification(
        "error",
        "Please fix the highlighted fields."
      );

      return;
    }

    setSaving(true);

    const payload = {
      first_name: form.first_name.trim(),
      last_name: form.last_name.trim(),
      email: form.email.trim(),
      phone: form.phone.trim() || null,
      date_of_birth:
        form.date_of_birth || null,
      gender: form.gender || null,
      company:
        form.company.trim() || null,
      job_title:
        form.job_title.trim() || null,
      country: form.country || null,
      city: form.city.trim() || null,
      address:
        form.address.trim() || null,
      postal_code:
        form.postal_code.trim() || null,
      department:
        form.department || null,
      employment_type:
        form.employment_type || null,
      experience_years:
        form.experience_years === ""
          ? null
          : Number(form.experience_years),
      salary:
        form.salary === ""
          ? null
          : Number(form.salary),
      newsletter: Boolean(form.newsletter),
      notes:
        form.notes.trim() || null
    };

    try {
      if (!supabase) {
        const existing =
          JSON.parse(
            localStorage.getItem(
              "browser-data-entry-people"
            ) || "[]"
          );

        if (editingPerson) {
          const next = existing.map((person) =>
            person.id === editingPerson.id
              ? {
                  ...person,
                  ...payload
                }
              : person
          );

          persistLocal(next);

          showNotification(
            "success",
            "Person updated successfully."
          );
        } else {
          const newPerson = {
            id: crypto.randomUUID(),
            ...payload,
            created_at:
              new Date().toISOString()
          };

          persistLocal([
            newPerson,
            ...existing
          ]);

          showNotification(
            "success",
            "Person added successfully."
          );
        }

        closeForm();
        return;
      }

      if (editingPerson) {
        const { error } = await supabase
          .from("people")
          .update(payload)
          .eq("id", editingPerson.id);

        if (error) {
          throw error;
        }

        showNotification(
          "success",
          "Person updated successfully."
        );
      } else {
        const { error } = await supabase
          .from("people")
          .insert(payload);

        if (error) {
          throw error;
        }

        showNotification(
          "success",
          "Person added successfully."
        );
      }

      closeForm();
      await loadPeople();
    } catch (error) {
      console.error(error);

      showNotification(
        "error",
        error?.message ||
          "Unable to save the person."
      );
    } finally {
      setSaving(false);
    }
  };

  const deletePerson = async (person) => {
    if (!person) return;

    try {
      if (!supabase) {
        const existing =
          JSON.parse(
            localStorage.getItem(
              "browser-data-entry-people"
            ) || "[]"
          );

        const next = existing.filter(
          (item) => item.id !== person.id
        );

        persistLocal(next);
      } else {
        const { error } = await supabase
          .from("people")
          .delete()
          .eq("id", person.id);

        if (error) {
          throw error;
        }

        await loadPeople();
      }

      setDeleteTarget(null);

      showNotification(
        "success",
        "Person deleted successfully."
      );
    } catch (error) {
      console.error(error);

      showNotification(
        "error",
        error?.message ||
          "Unable to delete the person."
      );
    }
  };

  const resetFilters = () => {
    setSearch("");
    setCountryFilter("");
    setDepartmentFilter("");
    setEmploymentFilter("");
    setPage(1);
  };

  const exportPeople = () => {
    const headers = [
      "First Name",
      "Last Name",
      "Email",
      "Phone",
      "Date of Birth",
      "Gender",
      "Company",
      "Job Title",
      "Country",
      "City",
      "Address",
      "Postal Code",
      "Department",
      "Employment Type",
      "Experience Years",
      "Salary",
      "Newsletter",
      "Notes",
      "Created At"
    ];

    const rows = filteredPeople.map(
      (person) => [
        person.first_name,
        person.last_name,
        person.email,
        person.phone,
        person.date_of_birth,
        person.gender,
        person.company,
        person.job_title,
        person.country,
        person.city,
        person.address,
        person.postal_code,
        person.department,
        person.employment_type,
        person.experience_years,
        person.salary,
        person.newsletter ? "Yes" : "No",
        person.notes,
        person.created_at
      ]
    );

    const escapeCSV = (value) => {
      const stringValue =
        value === null ||
        value === undefined
          ? ""
          : String(value);

      return `"${stringValue.replace(
        /"/g,
        '""'
      )}"`;
    };

    const csv = [
      headers.map(escapeCSV).join(","),
      ...rows.map((row) =>
        row.map(escapeCSV).join(",")
      )
    ].join("\n");

    const blob = new Blob(
      [csv],
      {
        type: "text/csv;charset=utf-8;"
      }
    );

    const url =
      URL.createObjectURL(blob);

    const link =
      document.createElement("a");

    link.href = url;
    link.download =
      "people-export.csv";

    document.body.appendChild(link);
    link.click();
    link.remove();

    URL.revokeObjectURL(url);

    showNotification(
      "success",
      "CSV export downloaded."
    );
  };

  const clearLocalData = () => {
    if (supabase) {
      showNotification(
        "error",
        "Local data controls are unavailable while Supabase is configured."
      );

      return;
    }

    localStorage.removeItem(
      "browser-data-entry-people"
    );

    setPeople([]);

    showNotification(
      "success",
      "Local data cleared."
    );
  };

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <aside className="fixed inset-y-0 left-0 hidden w-64 border-r border-slate-200 bg-white lg:block">
        <div className="flex h-16 items-center border-b border-slate-200 px-6">
          <div className="flex items-center gap-2.5">
            <div className="rounded-lg bg-slate-900 p-2 text-white">
              <Database size={18} />
            </div>

            <div>
              <p className="text-sm font-semibold">
                DataDesk
              </p>
              <p className="text-[11px] text-slate-400">
                People Management
              </p>
            </div>
          </div>
        </div>

        <nav className="space-y-1 p-4">
          <button
            type="button"
            onClick={() => setView("people")}
            className={[
              "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium",
              view === "people"
                ? "bg-slate-100 text-slate-900"
                : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"
            ].join(" ")}
          >
            <Users size={18} />
            People
          </button>

          <button
            type="button"
            onClick={() => setView("database")}
            className={[
              "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium",
              view === "database"
                ? "bg-slate-100 text-slate-900"
                : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"
            ].join(" ")}
          >
            <Database size={18} />
            Database
          </button>

          <button
            type="button"
            onClick={() => setView("settings")}
            className={[
              "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium",
              view === "settings"
                ? "bg-slate-100 text-slate-900"
                : "text-slate-500 hover:bg-slate-50 hover:text-slate-900"
            ].join(" ")}
          >
            <Settings size={18} />
            Settings
          </button>
        </nav>

        <div className="absolute bottom-4 left-4 right-4">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div className="flex items-center gap-2">
              {isConfigured ? (
                <>
                  <span className="h-2 w-2 rounded-full bg-emerald-500" />
                  <span className="text-xs font-medium text-slate-700">
                    Supabase connected
                  </span>
                </>
              ) : (
                <>
                  <span className="h-2 w-2 rounded-full bg-amber-500" />
                  <span className="text-xs font-medium text-slate-700">
                    Local demo mode
                  </span>
                </>
              )}
            </div>

            <p className="mt-2 text-[11px] leading-4 text-slate-400">
              {isConfigured
                ? "Records are stored in your Supabase PostgreSQL database."
                : "Add Supabase environment variables to enable database persistence."}
            </p>
          </div>
        </div>
      </aside>

      <div className="lg:pl-64">
        <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-slate-200 bg-white/95 px-5 backdrop-blur lg:px-8">
          <div>
            <h1 className="text-sm font-semibold text-slate-900">
              {view === "people"
                ? "People"
                : view === "database"
                ? "Database"
                : "Settings"}
            </h1>

            <p className="hidden text-xs text-slate-400 sm:block">
              Manage your people directory
            </p>
          </div>

          <div className="flex items-center gap-2">
            {view === "people" && (
              <>
                <button
                  type="button"
                  onClick={loadPeople}
                  disabled={loading}
                  aria-label="Refresh people"
                  title="Refresh"
                  className="rounded-xl border border-slate-200 bg-white p-2.5 text-slate-600 transition hover:bg-slate-50 disabled:opacity-50"
                >
                  <RefreshCw
                    size={17}
                    className={
                      loading
                        ? "animate-spin"
                        : ""
                    }
                  />
                </button>

                <button
                  type="button"
                  onClick={openCreateForm}
                  className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-3.5 py-2.5 text-sm font-medium text-white transition hover:bg-slate-800"
                >
                  <Plus size={17} />
                  <span className="hidden sm:inline">
                    Add Person
                  </span>
                </button>
              </>
            )}
          </div>
        </header>

        <main className="p-5 lg:p-8">
          {view === "people" && (
            <>
              <section className="mb-7 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <StatCard
                  icon={Users}
                  label="Total People"
                  value={stats.total}
                  description="All records"
                />

                <StatCard
                  icon={Plus}
                  label="Added Today"
                  value={stats.addedToday}
                  description="Created today"
                />

                <StatCard
                  icon={Building}
                  label="Companies"
                  value={stats.companies}
                  description="Unique companies"
                />

                <StatCard
                  icon={Globe}
                  label="Countries"
                  value={stats.countries}
                  description="Unique countries"
                />
              </section>

              <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
                <div className="border-b border-slate-200 p-4 lg:p-5">
                  <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
                    <div>
                      <h2 className="text-base font-semibold">
                        People Directory
                      </h2>
                      <p className="mt-0.5 text-xs text-slate-400">
                        {filteredPeople.length}{" "}
                        {filteredPeople.length === 1
                          ? "record"
                          : "records"}{" "}
                        found
                      </p>
                    </div>

                    <div className="flex flex-col gap-2 sm:flex-row">
                      <div className="relative">
                        <Search
                          size={16}
                          className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                        />

                        <input
                          id="people-search"
                          type="search"
                          value={search}
                          onChange={(event) => {
                            setSearch(
                              event.target.value
                            );
                            setPage(1);
                          }}
                          placeholder="Search people..."
                          className="w-full rounded-xl border border-slate-300 bg-white py-2.5 pl-9 pr-3 text-sm outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200 sm:w-64"
                        />
                      </div>

                      <select
                        id="country-filter"
                        value={countryFilter}
                        onChange={(event) => {
                          setCountryFilter(
                            event.target.value
                          );
                          setPage(1);
                        }}
                        aria-label="Filter by country"
                        className="rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                      >
                        <option value="">
                          All countries
                        </option>

                        {COUNTRIES.map(
                          (country) => (
                            <option
                              key={country}
                              value={country}
                            >
                              {country}
                            </option>
                          )
                        )}
                      </select>

                      <select
                        id="department-filter"
                        value={departmentFilter}
                        onChange={(event) => {
                          setDepartmentFilter(
                            event.target.value
                          );
                          setPage(1);
                        }}
                        aria-label="Filter by department"
                        className="rounded-xl border border-slate-300 bg-white px-3 py-2.5 text-sm outline-none focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                      >
                        <option value="">
                          All departments
                        </option>

                        {DEPARTMENTS.map(
                          (department) => (
                            <option
                              key={department}
                              value={department}
                            >
                              {department}
                            </option>
                          )
                        )}
                      </select>
                    </div>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <div className="flex items-center gap-2 text-xs font-medium text-slate-500">
                      <Filter size={14} />
                      More filters
                    </div>

                    <select
                      id="employment-filter"
                      value={employmentFilter}
                      onChange={(event) => {
                        setEmploymentFilter(
                          event.target.value
                        );
                        setPage(1);
                      }}
                      aria-label="Filter by employment type"
                      className="rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-600 outline-none focus:border-slate-400"
                    >
                      <option value="">
                        All employment types
                      </option>

                      {EMPLOYMENT_TYPES.map(
                        (type) => (
                          <option
                            key={type}
                            value={type}
                          >
                            {type}
                          </option>
                        )
                      )}
                    </select>

                    {(search ||
                      countryFilter ||
                      departmentFilter ||
                      employmentFilter) && (
                      <button
                        type="button"
                        onClick={resetFilters}
                        className="inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                      >
                        <X size={13} />
                        Clear filters
                      </button>
                    )}

                    <button
                      type="button"
                      onClick={exportPeople}
                      className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50"
                    >
                      <Download size={14} />
                      Export CSV
                    </button>
                  </div>
                </div>

                {loading ? (
                  <div className="flex min-h-[350px] items-center justify-center">
                    <div className="flex items-center gap-3 text-sm text-slate-500">
                      <RefreshCw
                        size={18}
                        className="animate-spin"
                      />
                      Loading people...
                    </div>
                  </div>
                ) : paginatedPeople.length === 0 ? (
                  <EmptyState
                    onAdd={openCreateForm}
                  />
                ) : (
                  <>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[900px] text-left">
                        <thead>
                          <tr className="border-b border-slate-200 bg-slate-50/80">
                            <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                              Person
                            </th>
                            <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                              Contact
                            </th>
                            <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                              Company
                            </th>
                            <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                              Location
                            </th>
                            <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                              Department
                            </th>
                            <th className="px-5 py-3 text-xs font-semibold uppercase tracking-wide text-slate-400">
                              Created
                            </th>
                            <th className="px-5 py-3 text-right text-xs font-semibold uppercase tracking-wide text-slate-400">
                              Actions
                            </th>
                          </tr>
                        </thead>

                        <tbody className="divide-y divide-slate-100">
                          {paginatedPeople.map(
                            (person) => (
                              <tr
                                key={person.id}
                                className="transition hover:bg-slate-50"
                              >
                                <td className="px-5 py-4">
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setSelectedPerson(
                                        person
                                      )
                                    }
                                    className="text-left"
                                  >
                                    <p className="text-sm font-semibold text-slate-900 hover:underline">
                                      {
                                        person.first_name
                                      }{" "}
                                      {
                                        person.last_name
                                      }
                                    </p>

                                    <p className="mt-0.5 text-xs text-slate-400">
                                      {person.job_title ||
                                        "No job title"}
                                    </p>
                                  </button>
                                </td>

                                <td className="px-5 py-4">
                                  <p className="text-sm text-slate-700">
                                    {person.email}
                                  </p>

                                  <p className="mt-0.5 text-xs text-slate-400">
                                    {person.phone ||
                                      "No phone"}
                                  </p>
                                </td>

                                <td className="px-5 py-4">
                                  <p className="text-sm text-slate-700">
                                    {person.company ||
                                      "—"}
                                  </p>
                                </td>

                                <td className="px-5 py-4">
                                  <p className="text-sm text-slate-700">
                                    {person.city ||
                                      "—"}
                                  </p>

                                  <p className="mt-0.5 text-xs text-slate-400">
                                    {person.country ||
                                      "—"}
                                  </p>
                                </td>

                                <td className="px-5 py-4">
                                  <div className="flex flex-col items-start gap-1">
                                    {person.department && (
                                      <span className="rounded-full bg-slate-100 px-2 py-1 text-xs font-medium text-slate-600">
                                        {
                                          person.department
                                        }
                                      </span>
                                    )}

                                    {person.employment_type && (
                                      <span className="text-xs text-slate-400">
                                        {
                                          person.employment_type
                                        }
                                      </span>
                                    )}
                                  </div>
                                </td>

                                <td className="px-5 py-4 text-sm text-slate-500">
                                  {formatDate(
                                    person.created_at
                                  )}
                                </td>

                                <td className="px-5 py-4">
                                  <div className="flex justify-end gap-1">
                                    <button
                                      type="button"
                                      onClick={() =>
                                        setSelectedPerson(
                                          person
                                        )
                                      }
                                      aria-label={`View ${person.first_name} ${person.last_name}`}
                                      title="View"
                                      className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                    >
                                      <FileText
                                        size={16}
                                      />
                                    </button>

                                    <button
                                      type="button"
                                      onClick={() =>
                                        openEditForm(
                                          person
                                        )
                                      }
                                      aria-label={`Edit ${person.first_name} ${person.last_name}`}
                                      title="Edit"
                                      className="rounded-lg p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                                    >
                                      <Edit3
                                        size={16}
                                      />
                                    </button>

                                    <button
                                      type="button"
                                      onClick={() =>
                                        setDeleteTarget(
                                          person
                                        )
                                      }
                                      aria-label={`Delete ${person.first_name} ${person.last_name}`}
                                      title="Delete"
                                      className="rounded-lg p-2 text-slate-400 hover:bg-red-50 hover:text-red-600"
                                    >
                                      <Trash2
                                        size={16}
                                      />
                                    </button>
                                  </div>
                                </td>
                              </tr>
                            )
                          )}
                        </tbody>
                      </table>
                    </div>

                    <div className="flex items-center justify-between border-t border-slate-200 px-5 py-4">
                      <p className="text-xs text-slate-400">
                        Showing{" "}
                        {Math.min(
                          (currentPage - 1) *
                            pageSize +
                            1,
                          filteredPeople.length
                        )}{" "}
                        to{" "}
                        {Math.min(
                          currentPage *
                            pageSize,
                          filteredPeople.length
                        )}{" "}
                        of{" "}
                        {filteredPeople.length}
                      </p>

                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() =>
                            setPage(
                              (current) =>
                                Math.max(
                                  1,
                                  current - 1
                                )
                            )
                          }
                          disabled={
                            currentPage === 1
                          }
                          aria-label="Previous page"
                          className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <ChevronLeft
                            size={16}
                          />
                        </button>

                        <span className="px-3 text-xs font-medium text-slate-600">
                          Page {currentPage} of{" "}
                          {totalPages}
                        </span>

                        <button
                          type="button"
                          onClick={() =>
                            setPage(
                              (current) =>
                                Math.min(
                                  totalPages,
                                  current + 1
                                )
                            )
                          }
                          disabled={
                            currentPage ===
                            totalPages
                          }
                          aria-label="Next page"
                          className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <ChevronRight
                            size={16}
                          />
                        </button>
                      </div>
                    </div>
                  </>
                )}
              </section>
            </>
          )}

          {view === "database" && (
            <section className="max-w-4xl">
              <div className="mb-6">
                <h2 className="text-xl font-semibold">
                  Database
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Connection and storage information.
                </p>
              </div>

              <div className="grid gap-5 md:grid-cols-2">
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex items-center gap-3">
                    <div className="rounded-xl bg-slate-100 p-3">
                      <Database
                        size={20}
                        className="text-slate-700"
                      />
                    </div>

                    <div>
                      <h3 className="font-semibold">
                        Storage
                      </h3>

                      <p className="text-xs text-slate-400">
                        Current persistence layer
                      </p>
                    </div>
                  </div>

                  <div className="mt-5 rounded-xl bg-slate-50 p-4">
                    <p className="text-sm font-medium">
                      {isConfigured
                        ? "Supabase PostgreSQL"
                        : "Browser LocalStorage"}
                    </p>

                    <p className="mt-1 text-xs leading-5 text-slate-500">
                      {isConfigured
                        ? "Your records are persisted in Supabase."
                        : "The application is running in local demo mode. Configure Supabase to persist records remotely."}
                    </p>
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex items-center gap-3">
                    <div className="rounded-xl bg-slate-100 p-3">
                      <ShieldCheck
                        size={20}
                        className="text-slate-700"
                      />
                    </div>

                    <div>
                      <h3 className="font-semibold">
                        Connection
                      </h3>

                      <p className="text-xs text-slate-400">
                        Database connectivity
                      </p>
                    </div>
                  </div>

                  <div className="mt-5 flex items-center gap-2 rounded-xl bg-slate-50 p-4">
                    <span
                      className={[
                        "h-2.5 w-2.5 rounded-full",
                        isConfigured
                          ? "bg-emerald-500"
                          : "bg-amber-500"
                      ].join(" ")}
                    />

                    <p className="text-sm font-medium">
                      {isConfigured
                        ? "Configured"
                        : "Not configured"}
                    </p>
                  </div>
                </div>
              </div>
            </section>
          )}

          {view === "settings" && (
            <section className="max-w-3xl">
              <div className="mb-6">
                <h2 className="text-xl font-semibold">
                  Settings
                </h2>

                <p className="mt-1 text-sm text-slate-500">
                  Application configuration and tools.
                </p>
              </div>

              <div className="space-y-5">
                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex items-start gap-4">
                    <div className="rounded-xl bg-slate-100 p-3">
                      <Sparkles
                        size={20}
                        className="text-slate-700"
                      />
                    </div>

                    <div>
                      <h3 className="font-semibold">
                        Browser Automation Ready
                      </h3>

                      <p className="mt-1 text-sm leading-6 text-slate-500">
                        The application uses semantic HTML,
                        stable element IDs, accessible labels,
                        real form controls, and deterministic
                        success/error states so browser agents
                        can interact with it reliably.
                      </p>
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <h3 className="font-semibold">
                    Data
                  </h3>

                  <p className="mt-1 text-sm text-slate-500">
                    Export or clear your locally stored demo
                    data.
                  </p>

                  <div className="mt-4 flex flex-wrap gap-2">
                    <button
                      type="button"
                      onClick={exportPeople}
                      className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-3.5 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                    >
                      <Download size={16} />
                      Export CSV
                    </button>

                    {!supabase && (
                      <button
                        type="button"
                        onClick={clearLocalData}
                        className="inline-flex items-center gap-2 rounded-xl border border-red-200 px-3.5 py-2.5 text-sm font-medium text-red-600 hover:bg-red-50"
                      >
                        <Trash2 size={16} />
                        Clear Local Data
                      </button>
                    )}
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm">
                  <div className="flex items-start gap-3">
                    <Info
                      size={19}
                      className="mt-0.5 text-slate-500"
                    />

                    <div>
                      <h3 className="font-semibold">
                        Supabase configuration
                      </h3>

                      <p className="mt-1 text-sm leading-6 text-slate-500">
                        Set VITE_SUPABASE_URL and
                        VITE_SUPABASE_ANON_KEY in your
                        environment file. Never expose a
                        Supabase service-role key in a frontend
                        application.
                      </p>
                    </div>
                  </div>
                </div>
              </div>
            </section>
          )}
        </main>
      </div>

      {notification && (
        <div
          role="status"
          aria-live="polite"
          className="fixed bottom-5 right-5 z-50 w-[calc(100vw-2.5rem)] max-w-sm"
        >
          <div
            className={[
              "flex items-start gap-3 rounded-2xl border bg-white p-4 shadow-xl",
              notification.type === "success"
                ? "border-emerald-200"
                : "border-red-200"
            ].join(" ")}
          >
            {notification.type === "success" ? (
              <CheckCircle
                size={19}
                className="mt-0.5 shrink-0 text-emerald-600"
              />
            ) : (
              <AlertCircle
                size={19}
                className="mt-0.5 shrink-0 text-red-600"
              />
            )}

            <p className="flex-1 text-sm text-slate-700">
              {notification.message}
            </p>

            <button
              type="button"
              onClick={() =>
                setNotification(null)
              }
              aria-label="Close notification"
              className="text-slate-400 hover:text-slate-700"
            >
              <X size={16} />
            </button>
          </div>
        </div>
      )}

      {showForm && (
        <div className="fixed inset-0 z-40 overflow-y-auto bg-slate-900/40 p-4 backdrop-blur-sm">
          <div className="flex min-h-full items-center justify-center py-8">
            <div className="w-full max-w-4xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl">
              <div className="flex items-center justify-between border-b border-slate-200 px-5 py-4 lg:px-7">
                <div>
                  <h2 className="text-lg font-semibold">
                    {editingPerson
                      ? "Edit Person"
                      : "Add Person"}
                  </h2>

                  <p className="mt-0.5 text-xs text-slate-400">
                    {editingPerson
                      ? "Update this person's information."
                      : "Enter the person's information below."}
                  </p>
                </div>

                <button
                  type="button"
                  onClick={closeForm}
                  aria-label="Close form"
                  className="rounded-xl p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                >
                  <X size={19} />
                </button>
              </div>

              <form
                onSubmit={submitForm}
                noValidate
              >
                <div className="max-h-[70vh] overflow-y-auto px-5 py-6 lg:px-7">
                  <div className="space-y-7">
                    <section>
                      <div className="mb-4">
                        <h3 className="text-sm font-semibold text-slate-900">
                          Basic Information
                        </h3>

                        <p className="mt-0.5 text-xs text-slate-400">
                          Personal and contact details.
                        </p>
                      </div>

                      <div className="grid gap-4 md:grid-cols-2">
                        <Field
                          label="First Name"
                          name="first_name"
                          value={form.first_name}
                          onChange={handleChange}
                          error={errors.first_name}
                          required
                          placeholder="e.g. John"
                        />

                        <Field
                          label="Last Name"
                          name="last_name"
                          value={form.last_name}
                          onChange={handleChange}
                          error={errors.last_name}
                          required
                          placeholder="e.g. Smith"
                        />

                        <Field
                          label="Email"
                          name="email"
                          type="email"
                          value={form.email}
                          onChange={handleChange}
                          error={errors.email}
                          required
                          placeholder="john.smith@example.com"
                        />

                        <Field
                          label="Phone"
                          name="phone"
                          type="tel"
                          value={form.phone}
                          onChange={handleChange}
                          error={errors.phone}
                          placeholder="+91 98765 43210"
                        />

                        <Field
                          label="Date of Birth"
                          name="date_of_birth"
                          type="date"
                          value={form.date_of_birth}
                          onChange={handleChange}
                          error={errors.date_of_birth}
                        />

                        <SelectField
                          label="Gender"
                          name="gender"
                          value={form.gender}
                          onChange={handleChange}
                          options={GENDERS}
                          error={errors.gender}
                        />
                      </div>
                    </section>

                    <section>
                      <div className="mb-4">
                        <h3 className="text-sm font-semibold text-slate-900">
                          Professional Information
                        </h3>

                        <p className="mt-0.5 text-xs text-slate-400">
                          Employment and organization details.
                        </p>
                      </div>

                      <div className="grid gap-4 md:grid-cols-2">
                        <Field
                          label="Company"
                          name="company"
                          value={form.company}
                          onChange={handleChange}
                          error={errors.company}
                          placeholder="e.g. Acme Corporation"
                        />

                        <Field
                          label="Job Title"
                          name="job_title"
                          value={form.job_title}
                          onChange={handleChange}
                          error={errors.job_title}
                          placeholder="e.g. Software Engineer"
                        />

                        <SelectField
                          label="Department"
                          name="department"
                          value={form.department}
                          onChange={handleChange}
                          options={DEPARTMENTS}
                          error={errors.department}
                        />

                        <SelectField
                          label="Employment Type"
                          name="employment_type"
                          value={form.employment_type}
                          onChange={handleChange}
                          options={EMPLOYMENT_TYPES}
                          error={errors.employment_type}
                        />

                        <Field
                          label="Experience Years"
                          name="experience_years"
                          type="number"
                          value={form.experience_years}
                          onChange={handleChange}
                          error={errors.experience_years}
                          placeholder="e.g. 5"
                        />

                        <Field
                          label="Salary"
                          name="salary"
                          type="number"
                          value={form.salary}
                          onChange={handleChange}
                          error={errors.salary}
                          placeholder="e.g. 75000"
                        />
                      </div>
                    </section>

                    <section>
                      <div className="mb-4">
                        <h3 className="text-sm font-semibold text-slate-900">
                          Location
                        </h3>

                        <p className="mt-0.5 text-xs text-slate-400">
                          Address and geographic information.
                        </p>
                      </div>

                      <div className="grid gap-4 md:grid-cols-2">
                        <SelectField
                          label="Country"
                          name="country"
                          value={form.country}
                          onChange={handleChange}
                          options={COUNTRIES}
                          error={errors.country}
                        />

                        <Field
                          label="City"
                          name="city"
                          value={form.city}
                          onChange={handleChange}
                          error={errors.city}
                          placeholder="e.g. New Delhi"
                        />

                        <Field
                          label="Postal Code"
                          name="postal_code"
                          value={form.postal_code}
                          onChange={handleChange}
                          error={errors.postal_code}
                          placeholder="e.g. 110001"
                        />

                        <div className="md:col-span-2">
                          <Field
                            label="Address"
                            name="address"
                            value={form.address}
                            onChange={handleChange}
                            error={errors.address}
                            placeholder="Street address"
                          />
                        </div>
                      </div>
                    </section>

                    <section>
                      <div className="mb-4">
                        <h3 className="text-sm font-semibold text-slate-900">
                          Additional Information
                        </h3>

                        <p className="mt-0.5 text-xs text-slate-400">
                          Preferences and notes.
                        </p>
                      </div>

                      <div className="space-y-4">
                        <div>
                          <label
                            htmlFor="field-notes"
                            className="mb-1.5 block text-sm font-medium text-slate-700"
                          >
                            Notes
                          </label>

                          <textarea
                            id="field-notes"
                            name="notes"
                            value={form.notes}
                            onChange={handleChange}
                            rows={4}
                            placeholder="Additional notes..."
                            className="w-full resize-none rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-900 outline-none placeholder:text-slate-400 focus:border-slate-500 focus:ring-2 focus:ring-slate-200"
                          />
                        </div>

                        <label
                          htmlFor="field-newsletter"
                          className="flex cursor-pointer items-start gap-3 rounded-xl border border-slate-200 bg-slate-50 p-4"
                        >
                          <input
                            id="field-newsletter"
                            name="newsletter"
                            type="checkbox"
                            checked={Boolean(
                              form.newsletter
                            )}
                            onChange={handleChange}
                            className="mt-0.5 h-4 w-4 rounded border-slate-300"
                          />

                          <span>
                            <span className="block text-sm font-medium text-slate-700">
                              Subscribe to newsletter
                            </span>

                            <span className="mt-0.5 block text-xs text-slate-400">
                              Allow this person to receive company updates.
                            </span>
                          </span>
                        </label>
                      </div>
                    </section>
                  </div>
                </div>

                <div className="flex flex-col-reverse gap-2 border-t border-slate-200 bg-slate-50/80 px-5 py-4 sm:flex-row sm:justify-end lg:px-7">
                  <button
                    type="button"
                    onClick={closeForm}
                    disabled={saving}
                    className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    Cancel
                  </button>

                  <button
                    type="submit"
                    disabled={saving}
                    className="inline-flex items-center justify-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {saving ? (
                      <>
                        <RefreshCw
                          size={16}
                          className="animate-spin"
                        />
                        Saving...
                      </>
                    ) : (
                      <>
                        <Check
                          size={16}
                        />
                        {editingPerson
                          ? "Save Changes"
                          : "Add Person"}
                      </>
                    )}
                  </button>
                </div>
              </form>
            </div>
          </div>
        </div>
      )}

      {selectedPerson && (
        <div className="fixed inset-0 z-40 overflow-y-auto bg-slate-900/40 p-4 backdrop-blur-sm">
          <div className="flex min-h-full items-center justify-center py-8">
            <div className="w-full max-w-2xl overflow-hidden rounded-3xl border border-slate-200 bg-white shadow-2xl">
              <div className="flex items-center justify-between border-b border-slate-200 px-6 py-5">
                <div>
                  <p className="text-xs font-medium uppercase tracking-wider text-slate-400">
                    Person Details
                  </p>

                  <h2 className="mt-1 text-xl font-semibold">
                    {
                      selectedPerson.first_name
                    }{" "}
                    {
                      selectedPerson.last_name
                    }
                  </h2>
                </div>

                <button
                  type="button"
                  onClick={() =>
                    setSelectedPerson(null)
                  }
                  aria-label="Close person details"
                  className="rounded-xl p-2 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                >
                  <X size={19} />
                </button>
              </div>

              <div className="max-h-[70vh] overflow-y-auto px-6 py-6">
                <div className="grid gap-4 sm:grid-cols-2">
                  {[
                    [
                      "First Name",
                      selectedPerson.first_name
                    ],
                    [
                      "Last Name",
                      selectedPerson.last_name
                    ],
                    [
                      "Email",
                      selectedPerson.email
                    ],
                    [
                      "Phone",
                      selectedPerson.phone
                    ],
                    [
                      "Date of Birth",
                      selectedPerson.date_of_birth
                    ],
                    [
                      "Gender",
                      selectedPerson.gender
                    ],
                    [
                      "Company",
                      selectedPerson.company
                    ],
                    [
                      "Job Title",
                      selectedPerson.job_title
                    ],
                    [
                      "Country",
                      selectedPerson.country
                    ],
                    [
                      "City",
                      selectedPerson.city
                    ],
                    [
                      "Postal Code",
                      selectedPerson.postal_code
                    ],
                    [
                      "Department",
                      selectedPerson.department
                    ],
                    [
                      "Employment Type",
                      selectedPerson.employment_type
                    ],
                    [
                      "Experience Years",
                      selectedPerson.experience_years
                    ],
                    [
                      "Salary",
                      formatCurrency(
                        selectedPerson.salary
                      )
                    ],
                    [
                      "Newsletter",
                      selectedPerson.newsletter
                        ? "Subscribed"
                        : "Not subscribed"
                    ],
                    [
                      "Created At",
                      formatDate(
                        selectedPerson.created_at
                      )
                    ]
                  ].map(
                    ([label, value]) => (
                      <div
                        key={label}
                        className="rounded-xl border border-slate-200 bg-slate-50 p-3.5"
                      >
                        <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                          {label}
                        </p>

                        <p className="mt-1 text-sm text-slate-800">
                          {value === null ||
                          value === undefined ||
                          value === ""
                            ? "—"
                            : String(value)}
                        </p>
                      </div>
                    )
                  )}

                  <div className="sm:col-span-2">
                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3.5">
                      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                        Address
                      </p>

                      <p className="mt-1 text-sm text-slate-800">
                        {selectedPerson.address ||
                          "—"}
                      </p>
                    </div>
                  </div>

                  <div className="sm:col-span-2">
                    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3.5">
                      <p className="text-[11px] font-medium uppercase tracking-wide text-slate-400">
                        Notes
                      </p>

                      <p className="mt-1 whitespace-pre-wrap text-sm text-slate-800">
                        {selectedPerson.notes ||
                          "—"}
                      </p>
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex justify-end gap-2 border-t border-slate-200 bg-slate-50/80 px-6 py-4">
                <button
                  type="button"
                  onClick={() => {
                    const person =
                      selectedPerson;

                    setSelectedPerson(null);

                    openEditForm(person);
                  }}
                  className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
                >
                  <Edit3 size={16} />
                  Edit
                </button>

                <button
                  type="button"
                  onClick={() =>
                    setSelectedPerson(null)
                  }
                  className="rounded-xl bg-slate-900 px-4 py-2.5 text-sm font-medium text-white hover:bg-slate-800"
                >
                  Close
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-6 shadow-2xl">
            <div className="flex items-start gap-4">
              <div className="rounded-xl bg-red-50 p-3 text-red-600">
                <AlertCircle size={21} />
              </div>

              <div>
                <h2 className="font-semibold text-slate-900">
                  Delete person?
                </h2>

                <p className="mt-1 text-sm leading-5 text-slate-500">
                  This will permanently delete{" "}
                  <strong>
                    {deleteTarget.first_name}{" "}
                    {deleteTarget.last_name}
                  </strong>
                  . This action cannot be undone.
                </p>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() =>
                  setDeleteTarget(null)
                }
                className="rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
              >
                Cancel
              </button>

              <button
                type="button"
                onClick={() =>
                  deletePerson(deleteTarget)
                }
                className="inline-flex items-center gap-2 rounded-xl bg-red-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-red-700"
              >
                <Trash2 size={16} />
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;