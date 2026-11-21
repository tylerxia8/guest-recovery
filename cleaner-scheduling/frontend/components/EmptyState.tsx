import Image from "next/image";

type EmptyStateVariant =
  | "dashboard"
  | "cleaners"
  | "houses"
  | "exceptions"
  | "portal-no-jobs"
  | "onboarding"
  | "first-run"
  | "success";

interface EmptyStateProps {
  variant: EmptyStateVariant;
  title: string;
  description?: string;
  children?: React.ReactNode;
}

const ILLUSTRATION_MAP: Record<EmptyStateVariant, { src: string; width: number; height: number }> = {
  dashboard: {
    src: "/illustrations/G5-empty-dashboard-light.svg",
    width: 480,
    height: 320,
  },
  cleaners: {
    src: "/illustrations/G6-empty-cleaners-light.svg",
    width: 480,
    height: 320,
  },
  houses: {
    src: "/illustrations/G7-empty-houses-light.svg",
    width: 480,
    height: 320,
  },
  exceptions: {
    src: "/illustrations/G8-empty-exceptions-light.svg",
    width: 400,
    height: 280,
  },
  "portal-no-jobs": {
    src: "/illustrations/G9-portal-no-jobs-light.svg",
    width: 400,
    height: 280,
  },
  onboarding: {
    src: "/illustrations/G10-onboarding-welcome-light.svg",
    width: 560,
    height: 360,
  },
  "first-run": {
    src: "/illustrations/G11-manager-first-run-light.svg",
    width: 640,
    height: 400,
  },
  success: {
    src: "/illustrations/G12-success-light.svg",
    width: 240,
    height: 240,
  },
};

export function EmptyState({ variant, title, description, children }: EmptyStateProps) {
  const illustration = ILLUSTRATION_MAP[variant];

  return (
    <div className="empty-state">
      <Image
        src={illustration.src}
        width={illustration.width}
        height={illustration.height}
        alt=""
        className="empty-state-illustration"
        aria-hidden="true"
        priority={false}
      />
      <h2 className="empty-state-title">{title}</h2>
      {description && <p className="empty-state-description">{description}</p>}
      {children}
    </div>
  );
}
