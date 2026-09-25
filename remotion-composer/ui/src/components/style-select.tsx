import React, { useEffect, useState } from "react";
import { EditStyle, api } from "../api/client";

export const StyleSelect: React.FC<{
  value: string;
  onPick: (style: EditStyle | null) => void;
  allowEmpty?: boolean;
  refreshKey?: number;
}> = ({ value, onPick, allowEmpty = true, refreshKey = 0 }) => {
  const [styles, setStyles] = useState<EditStyle[]>([]);

  useEffect(() => {
    api.listEditStyles().then(setStyles).catch(() => undefined);
  }, [refreshKey]);

  return (
    <label className="field">
      Mẫu dựng
      <select
        value={value}
        onChange={(e) => {
          const id = e.target.value;
          onPick(styles.find((s) => s.id === id) || null);
        }}
      >
        {allowEmpty && <option value="">Không dùng mẫu — chỉnh tay bên dưới</option>}
        {styles.map((style) => (
          <option key={style.id} value={style.id}>
            {style.title}
          </option>
        ))}
      </select>
      <span className="hint">
        {styles.length === 0
          ? "Chưa có mẫu — chỉnh tay, hoặc vào tab Mẫu dựng để lưu mẫu mới."
          : "Chọn mẫu có sẵn rồi chỉnh thêm nếu cần. Dùng lại cho nhiều nhà sáng tạo."}
      </span>
    </label>
  );
};
