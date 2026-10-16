select table_name,column_name,data_type,column_default,is_nullable
from information_schema.columns
where table_schema='public' and table_name='ezynotify' order by ordinal_position;
