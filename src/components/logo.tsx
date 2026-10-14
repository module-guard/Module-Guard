import Image from "next/image";
export function Logo({
  small = false,
  dark = false,
}: {
  small?: boolean;
  dark?: boolean;
}) {
  return (
    <div className="flex items-center gap-2.5">
      <Image
        src={dark ? "/brand/mark-on-dark.png" : "/brand/mark.png"}
        alt=""
        width={32}
        height={32}
        priority
      />
      {!small && (
        <span className="text-[21px] font-bold tracking-[-.8px]">
          ModuleGuard<span className="text-brand">.</span>
        </span>
      )}
    </div>
  );
}
